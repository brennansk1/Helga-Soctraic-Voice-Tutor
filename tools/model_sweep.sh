#!/bin/bash
#
# model_sweep.sh — run model_gate.py across candidates, ONE AT A TIME, without
#                  taking the machine down.
#
# WHY A WRAPPER RATHER THAN A LOOP
# --------------------------------
# `model_gate.py` evaluates ONE model and has no memory management, which is
# correct for what it is. Looping it across candidates is what OOM'd this
# machine once already: each `ollama` call leaves the model resident with a
# keep-alive, so run two 14 GB models back to back on 24 GB of RAM and the
# second load lands on top of the first. Measured at the time: 12.8 GB
# resident, 0.06 GB free.
#
# `OLLAMA_MAX_LOADED_MODELS=1` makes a swap EVICT rather than ADD, which is
# necessary but not sufficient — eviction happens when the new model loads, so
# the peak still briefly holds both. So this script unloads explicitly and
# waits for the kernel to agree the memory came back before starting the next.
#
# THE GUARD DEFERS TO services/common/memory_guard
# -------------------------------------------------
# See mem_state() below for why this does not gate on a percentage, and does
# not invent its own thresholds: the calibration has been got wrong four times
# in this repo, and it now lives in exactly one module.
#
# USAGE
#   ./tools/model_sweep.sh                 # all candidates
#   ./tools/model_sweep.sh <model> [...]   # specific ones
set -uo pipefail

OLLAMA=/usr/local/bin/ollama
URL="${OLLAMA_HOST:-http://localhost:11434}"
OUT="${SWEEP_OUT:-docs/baselines/model_sweep_$(date +%Y%m%d_%H%M).json}"

RECOVER_WAIT=180          # seconds to allow for memory to come back

CANDIDATES=(
  "hf.co/unsloth/GLM-4.7-Flash-REAP-23B-A3B-GGUF:Q4_K_M"
  "hf.co/unsloth/Qwen3.6-27B-MTP-GGUF:Q4_K_M"
  "hf.co/bartowski/google_gemma-3-12b-it-GGUF:Q4_K_M"
  "qwen3:14b-q4_K_M"
)
[ $# -gt 0 ] && CANDIDATES=("$@")

log()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# ONE CALIBRATION, ONE PLACE.
#
# This script first gated on `memory_pressure`'s free PERCENTAGE, and that was
# wrong for the fourth time in this codebase's history. With a 13.5 GB model
# legitimately resident and everything healthy, measured simultaneously:
#
#     free percentage        16%   <- below this script's 20% stop floor
#     kernel pressure level  2     (WARN — throttle, not stop)
#     memory_guard verdict   OK    (2.02 GB available, floor 1.5 GB)
#
# The percentage counts a resident model as "used", so the number is low
# exactly when the sweep is doing its job, and the sweep would have aborted
# itself between candidates. `services/common/memory_guard` already encodes
# the calibration — three passes of it, documented in the module — so this
# asks that module rather than inventing a fifth opinion.
mem_state() {
  python3 -c "
import sys; sys.path.insert(0, '$PWD')
try:
    from services.common import memory_guard as mg
    lvl = mg.macos_pressure_level() or 1
    s = mg.snapshot()
    reason = mg.pressure_reason(s)
    print(f'{lvl}|{s.available_gb:.2f}|{reason or \"OK\"}')
except Exception as e:
    print(f'1|99|OK (guard unavailable: {e})')
" 2>/dev/null
}

resident() {
  curl -sf --max-time 10 "$URL/api/ps" 2>/dev/null \
    | python3 -c 'import sys,json;[print(m["name"]) for m in json.load(sys.stdin).get("models",[])]' 2>/dev/null
}

unload_all() {
  local any=0
  for n in $(resident); do
    any=1
    log "unloading $n"
    curl -sf --max-time 60 "$URL/api/generate" \
      -d "{\"model\":\"$n\",\"keep_alive\":0}" >/dev/null 2>&1
  done
  [ "$any" = 1 ] && sleep 8
  return 0
}

# Wait until the kernel agrees the memory came back. Without this the next
# load starts while the previous model is still being torn down, and the peak
# is the sum of both — precisely the OOM this script exists to avoid.
await_headroom() {
  local waited=0 state lvl avail reason
  while :; do
    state=$(mem_state); lvl=${state%%|*}; avail=$(echo "$state" | cut -d'|' -f2)
    reason=${state##*|}

    # CRITICAL is the only unconditional stop. The kernel is the authority on
    # whether it is thrashing; nothing here second-guesses it.
    if [ "$lvl" -ge 4 ]; then
      if [ "$waited" -ge "$RECOVER_WAIT" ]; then
        return 1
      fi
      warn "kernel reports CRITICAL pressure — waiting (${waited}s)"
      sleep 15; waited=$((waited + 15)); continue
    fi

    if [ "$reason" = "OK" ]; then
      log "memory: level $lvl, ${avail} GB available — ok"
      return 0
    fi

    if [ "$waited" -ge "$RECOVER_WAIT" ]; then
      warn "memory still tight after ${waited}s ($reason) — stopping"
      return 1
    fi
    warn "memory: $reason — waiting for recovery (${waited}s)"
    sleep 15; waited=$((waited + 15))
  done
}

trap 'echo; warn "interrupted — unloading before exit"; unload_all; exit 130' INT TERM

command -v "$OLLAMA" >/dev/null || die "ollama not found at $OLLAMA"
curl -sf --max-time 5 "$URL/api/version" >/dev/null || die "Ollama is not running"
mkdir -p "$(dirname "$OUT")"

echo
log "candidates : ${#CANDIDATES[@]}"
log "results    : $OUT"
log "guard      : services/common/memory_guard (kernel pressure level)"
echo

# Start from a clean slate: anything already resident is another model's
# memory, and it is not ours to leave in place.
unload_all
await_headroom || die "memory is already too tight to start"

echo "[" > "$OUT"
first=1
# Listed ONCE, into a variable. `"$OLLAMA" list | grep -q` looks obvious and is
# wrong under `set -o pipefail`: grep -q exits at the first match, ollama takes
# SIGPIPE, and pipefail reports the whole pipeline as failed — so every model
# present on disk was skipped as "not installed", and the sweep completed in
# four seconds having measured nothing.
INSTALLED=$("$OLLAMA" list 2>/dev/null)

for model in "${CANDIDATES[@]}"; do
  case "$INSTALLED" in
    *"${model%%:*}"*) ;;
    *) warn "not installed, skipping: $model"; continue ;;
  esac

  echo
  log "──────────────────────────────────────────────────────────"
  log "$model"
  if ! await_headroom; then
    die "memory did not recover — stopping the sweep here.
       Results so far are in $OUT"
  fi

  # Tee to a per-model log. Capturing stdout into a variable alone left a
  # multi-hour run completely opaque — no way to tell progress from a hang.
  slug=$(echo "$model" | tr '/:' '__')
  mlog="$(dirname "$OUT")/${slug}.log"
  started=$(date +%s)
  result=$(python3 tools/model_gate.py --model "$model" --json 2>"$mlog")
  rc=$?
  log "detail log: $mlog"
  elapsed=$(( $(date +%s) - started ))

  if [ $rc -ne 0 ] || [ -z "$result" ]; then
    warn "gate failed for $model (rc=$rc, ${elapsed}s)"
    result="{\"model\":\"$model\",\"error\":\"gate failed rc=$rc\"}"
  else
    ok "$model — ${elapsed}s"
    echo "$result" | python3 -c "
import sys,json
try:
    d=json.load(sys.stdin)
    for k in ('contract','tokens_per_sec','seconds_per_concept','verdict'):
        if k in d: print(f'      {k}: {d[k]}')
except Exception: pass
" 2>/dev/null
  fi

  [ $first = 0 ] && echo "," >> "$OUT"
  first=0
  echo "$result" | python3 -c "
import sys,json
d=json.load(sys.stdin); d['elapsed_seconds']=$elapsed
print(json.dumps(d,indent=2))" >> "$OUT" 2>/dev/null || echo "$result" >> "$OUT"

  # ALWAYS unload before the next candidate, pass or fail.
  unload_all
done
echo "]" >> "$OUT"

echo
unload_all
ok "sweep complete — $OUT"
log "final memory: $(mem_state)"
echo
