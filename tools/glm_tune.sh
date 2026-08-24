#!/bin/bash
#
# glm_tune.sh — can GLM-4.7-Flash be made to do the builder's job?
#
# WHAT THE SWEEP FOUND
# --------------------
# GLM fails the depth contract in two distinct ways, which matters because
# they have different causes:
#
#   The Pythagorean Theorem    211w   5.9s   too short, EVERY required
#                                            section missing
#   Eigenvalues/Eigenvectors  1644w 130.1s   REPETITION — the degeneration
#                                            mode that disqualified the
#                                            ternary 27B
#   Natural Selection          803w  49.4s   fail
#
# THE LEADING HYPOTHESIS IS NOT ABOUT GLM AT ALL
# -----------------------------------------------
# Ollama starts its runner with `-c 4096` (verified in the llama-server
# command line) and `num_ctx` is set nowhere in this codebase. GLM's trained
# context is 202,752. The builder prompt — template plus research brief plus
# hydrated source text — was measured at roughly 4,800 tokens.
#
# If the prompt exceeds the context, the FRONT of it is what survives and the
# tail is cut. The required-section spec lives in that tail. That would
# explain a model emitting none of the required sections while otherwise
# producing fluent prose: it never saw the spec.
#
# If that is right it is a pipeline bug affecting every model including the
# one in production, and GLM is merely the first to make it visible.
#
# THE OTHER TWO KNOBS
# -------------------
#   thinking   GLM answered an earlier smoke test with "1. **Analyze the
#              user's request" — chain-of-thought in the content field. This
#              repo has been bitten by exactly that before (see llm_utils:
#              reasoning_effort="none" was worth a 4x speedup and fixed
#              blank responses).
#   repeat     1644 words of repetition is what a too-low repeat_penalty
#              looks like under a long generation.
#
# MEMORY
# ------
# Context is not free: KV cache scales with it, on top of 13.5 GB of weights
# on a 24 GB machine. Each configuration is checked against memory_guard
# before it runs and the model is unloaded after, exactly as model_sweep does.
#
#   ./tools/glm_tune.sh
set -uo pipefail

OLLAMA=/usr/local/bin/ollama
URL="${OLLAMA_HOST:-http://localhost:11434}"
MODEL="hf.co/unsloth/GLM-4.7-Flash-REAP-23B-A3B-GGUF:Q4_K_M"
OUTDIR="docs/baselines/glm_tune_$(date +%Y%m%d_%H%M)"

log()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

mem_state() {
  python3 -c "
import sys; sys.path.insert(0, '$PWD')
try:
    from services.common import memory_guard as mg
    s = mg.snapshot()
    print(f'{mg.macos_pressure_level() or 1}|{s.available_gb:.2f}|{mg.pressure_reason(s) or \"OK\"}')
except Exception as e:
    print(f'1|99|OK ({e})')
" 2>/dev/null
}

unload_all() {
  for n in $(curl -sf --max-time 10 "$URL/api/ps" 2>/dev/null \
      | python3 -c 'import sys,json;[print(m["name"]) for m in json.load(sys.stdin).get("models",[])]' 2>/dev/null); do
    curl -sf --max-time 60 "$URL/api/generate" -d "{\"model\":\"$n\",\"keep_alive\":0}" >/dev/null 2>&1
  done
  sleep 6
}

# Ollama reads OLLAMA_CONTEXT_LENGTH when the SERVER starts, not per request,
# so changing it means restarting Ollama. launchctl setenv makes it visible to
# the app the launcher spawns.
restart_ollama_with_ctx() {
  local ctx="$1"
  unload_all
  launchctl setenv OLLAMA_CONTEXT_LENGTH "$ctx"
  launchctl setenv OLLAMA_MAX_LOADED_MODELS 1
  pkill -TERM -f "Ollama.app/Contents/MacOS/Ollama" 2>/dev/null
  sleep 3
  pkill -TERM -f "Ollama.app/Contents/Resources/ollama" 2>/dev/null
  sleep 3
  open -a Ollama >/dev/null 2>&1
  for _ in $(seq 1 40); do
    curl -sf --max-time 3 "$URL/api/version" >/dev/null 2>&1 && break
    sleep 2
  done
  curl -sf --max-time 5 "$URL/api/version" >/dev/null || die "Ollama did not restart"
}

# Prove the setting took effect rather than assuming it. The runner's own
# command line is the only honest evidence — an env var that the app ignored
# would otherwise look identical to one it honoured.
actual_ctx() {
  pgrep -lf "llama-server" 2>/dev/null | grep -oE '\-c [0-9]+' | head -1 | awk '{print $2}'
}

trap 'echo; warn "interrupted — unloading"; unload_all; exit 130' INT TERM

mkdir -p "$OUTDIR"
curl -sf --max-time 5 "$URL/api/version" >/dev/null || die "Ollama is not running"

echo
log "model  : $MODEL"
log "results: $OUTDIR"
echo

#   label            ctx     think  extra note
CONFIGS=(
  "baseline|4096|no|as the sweep ran it — the control"
  "ctx16k|16384|no|prompt should now fit; the leading hypothesis"
  "ctx32k|32768|no|headroom for the research brief too"
  "ctx16k-think|16384|yes|does deliberation help or eat the budget"
)

for cfg in "${CONFIGS[@]}"; do
  IFS='|' read -r label ctx think note <<< "$cfg"
  echo
  log "──────────────────────────────────────────────────────────"
  log "$label  (ctx=$ctx think=$think)"
  log "$note"

  state=$(mem_state)
  if [ "${state%%|*}" -ge 4 ]; then
    warn "kernel reports CRITICAL pressure — skipping $label"
    continue
  fi

  restart_ollama_with_ctx "$ctx"

  mlog="$OUTDIR/${label}.log"
  log "live log: $mlog"
  started=$(date +%s)
  HELGA_LLM_THINK="$think" PYTHONUNBUFFERED=1 \
    python3 tools/model_gate.py --model "$MODEL" --json > "$mlog" 2>&1
  elapsed=$(( $(date +%s) - started ))

  got=$(actual_ctx)
  log "runner started with -c ${got:-unknown} (asked for $ctx)"
  [ -n "$got" ] && [ "$got" != "$ctx" ] && \
    warn "OLLAMA_CONTEXT_LENGTH did NOT take effect — treat this row as the baseline repeated"

  passes=$(grep -acE '\[ok\]' "$mlog" 2>/dev/null || echo 0)
  fails=$(grep -acE '\[fail\]' "$mlog" 2>/dev/null || echo 0)
  reps=$(grep -ac 'REPETITION' "$mlog" 2>/dev/null || echo 0)
  ok "$label — ${elapsed}s: ${passes} pass / ${fails} fail, ${reps} repetition"
  grep -aE '\[(ok|fail)\]' "$mlog" 2>/dev/null | tail -6 | sed 's/^/        /'

  echo "$label ctx=$ctx think=$think pass=$passes fail=$fails repetition=$reps seconds=$elapsed actual_ctx=${got:-unknown}" \
    >> "$OUTDIR/summary.txt"
  unload_all
done

echo
log "──────────────────────────────────────────────────────────"
cat "$OUTDIR/summary.txt" 2>/dev/null | sed 's/^/  /'
echo
# Leave the box as we found it rather than silently pinning a context length.
launchctl unsetenv OLLAMA_CONTEXT_LENGTH
warn "OLLAMA_CONTEXT_LENGTH unset again — set it deliberately if a row won"
ok "done — $OUTDIR"
echo
