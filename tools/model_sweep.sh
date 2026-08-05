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
# THE GUARD IS THE KERNEL'S PRESSURE LEVEL, NOT FREE BYTES
# --------------------------------------------------------
# `vm_stat` free-page arithmetic understates available memory badly on macOS —
# it reported 1.0 GB "available" at the same moment the kernel reported 81%
# free, because cached file pages are reclaimable and do not show as free.
# Gating on that number would refuse to run on a perfectly healthy machine.
# `memory_pressure` is the signal the kernel actually acts on, so it is the
# signal used here:
#
#     >= 40% free   proceed
#     20-39% free   warn, wait for recovery, then proceed
#     <  20% free   STOP — do not start another model
#
# USAGE
#   ./tools/model_sweep.sh                 # all candidates
#   ./tools/model_sweep.sh <model> [...]   # specific ones
set -uo pipefail

OLLAMA=/usr/local/bin/ollama
URL="${OLLAMA_HOST:-http://localhost:11434}"
OUT="${SWEEP_OUT:-docs/baselines/model_sweep_$(date +%Y%m%d_%H%M).json}"

# Floors, in "percent free" as memory_pressure reports it.
STOP_BELOW=20
WARN_BELOW=40
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

pressure_free() {
  # "System-wide memory free percentage: 81%" -> 81
  memory_pressure 2>/dev/null | tail -1 | grep -oE '[0-9]+' | tail -1
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
  local waited=0 p
  while :; do
    p=$(pressure_free)
    [ -z "$p" ] && { warn "cannot read memory pressure — proceeding cautiously"; return 0; }
    if [ "$p" -ge "$WARN_BELOW" ]; then
      log "memory: ${p}% free — ok"
      return 0
    fi
    if [ "$waited" -ge "$RECOVER_WAIT" ]; then
      if [ "$p" -lt "$STOP_BELOW" ]; then
        return 1                      # caller aborts the sweep
      fi
      warn "memory: ${p}% free after ${waited}s — low but above the floor, continuing"
      return 0
    fi
    warn "memory: ${p}% free — waiting for recovery (${waited}s)"
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
log "guard      : stop below ${STOP_BELOW}% free, warn below ${WARN_BELOW}%"
echo

# Start from a clean slate: anything already resident is another model's
# memory, and it is not ours to leave in place.
unload_all
await_headroom || die "memory is already below ${STOP_BELOW}% free — not starting"

echo "[" > "$OUT"
first=1
for model in "${CANDIDATES[@]}"; do
  if ! "$OLLAMA" list 2>/dev/null | grep -qF "${model%%:*}"; then
    warn "not installed, skipping: $model"
    continue
  fi

  echo
  log "──────────────────────────────────────────────────────────"
  log "$model"
  if ! await_headroom; then
    die "memory dropped below ${STOP_BELOW}% free — stopping the sweep here.
       Results so far are in $OUT"
  fi

  started=$(date +%s)
  result=$(python3 tools/model_gate.py --model "$model" --json 2>/dev/null)
  rc=$?
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
log "final memory: $(pressure_free)% free"
echo
