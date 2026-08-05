#!/bin/bash
#
# model_store.sh — move Ollama's model store to the Passport, and fetch the
#                  models we want to benchmark.
#
# WHY THIS EXISTS
# ---------------
# The internal disk is 88% full with 53 GB free, and 36 GB of that is Ollama
# models. The survey candidates are another ~37 GB. The Passport has 1.7 TB
# free and already holds an AI-Models tree, so the model store belongs there.
#
# WHAT MAKES THIS RISKY, AND WHAT THE SCRIPT DOES ABOUT IT
# --------------------------------------------------------
# Moving a 36 GB store means a window where the models exist in neither the old
# nor the new location. If that is interrupted — Ctrl-C, the drive sleeping, a
# panic — Ollama comes back with an empty store and every course build fails
# with "model not found", which looks like a Helga bug rather than a half-
# finished move.
#
# So:
#   * the move is a COPY-then-verify-then-remove, never a bare mv;
#   * a trap catches INT/TERM and leaves the ORIGINAL intact;
#   * a marker file records which phase we were in, so a re-run resumes
#     instead of starting over or double-deleting;
#   * the drive is checked for being mounted AND writable before anything
#     starts, because an auto-unlock that silently failed presents as a
#     missing directory.
#
# USAGE
#   ./scripts/model_store.sh status     what is where, how much space
#   ./scripts/model_store.sh migrate    move the store to the Passport
#   ./scripts/model_store.sh fetch      download the benchmark candidates
#   ./scripts/model_store.sh rollback   put the store back on the internal disk
#
set -uo pipefail

DRIVE="/Volumes/My Passport"
DEST="$DRIVE/AI-Models/ollama"
SRC="${OLLAMA_MODELS:-$HOME/.ollama/models}"
MARKER="$DRIVE/AI-Models/.ollama-migration-state"
OLLAMA_HOST="${OLLAMA_HOST:-http://localhost:11434}"

# Models to benchmark. Sizes are the Q4_K_M figures measured from the HF API.
#   name                                            approx GB   why
FETCH_LIST=(
  "hf.co/unsloth/GLM-4.7-Flash-REAP-23B-A3B-GGUF:Q4_K_M|13.1|MoE, ~3B active — fastest big option"
  "hf.co/unsloth/Qwen3.6-27B-MTP-GGUF:Q4_K_M|15.9|newest Qwen + multi-token prediction"
  "hf.co/bartowski/google_gemma-3-12b-it-GGUF:Q4_K_M|6.8|dev-safe chat alternative"
)

log()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; exit 1; }

# --- safety -----------------------------------------------------------------

INTERRUPTED=0
on_signal() {
  INTERRUPTED=1
  echo
  warn "interrupted — stopping cleanly, the ORIGINAL store is untouched"
  # Restart Ollama on the way out. Leaving it stopped means no LLM at all,
  # which is a worse outcome than the half-finished copy we just abandoned.
  ollama_running || start_ollama
  warn "re-run the same command to resume (rsync --partial picks up where it stopped)"
  exit 130
}
trap on_signal INT TERM

require_drive() {
  mount | grep -q "on $DRIVE " || die "Passport is not mounted (auto-unlock may have failed)"
  # Mounted is not the same as writable: a locked or read-only mount looks
  # identical to `ls` until you try to write.
  local probe="$DRIVE/.helga-write-probe.$$"
  touch "$probe" 2>/dev/null || die "Passport is mounted but NOT WRITABLE"
  rm -f "$probe"
}

ollama_running() { curl -sf --max-time 5 "$OLLAMA_HOST/api/version" >/dev/null 2>&1; }

# Unload every resident model first. Ollama holds files open, and on a 24 GB
# machine two resident models is also how we OOM'd once already.
unload_models() {
  ollama_running || return 0
  local names
  names=$(curl -sf --max-time 5 "$OLLAMA_HOST/api/ps" \
          | python3 -c 'import sys,json;[print(m["name"]) for m in json.load(sys.stdin).get("models",[])]' 2>/dev/null)
  for n in $names; do
    log "unloading $n"
    curl -sf --max-time 30 "$OLLAMA_HOST/api/generate" \
      -d "{\"model\":\"$n\",\"keep_alive\":0}" >/dev/null 2>&1
  done
}

stop_ollama() {
  unload_models
  ollama_running || { ok "Ollama already stopped"; return 0; }

  log "stopping Ollama"
  # Escalating, gentlest first. Killing only the server is useless: the menu-bar
  # app SUPERVISES it and restarts it within a second, so the store would still
  # be held open. The supervisor has to go first.
  #
  # `osascript -e 'quit app "Ollama"'` is tried but is not reliable here — it
  # returns "User canceled (-128)" because the menu-bar app declines the Apple
  # Event. Hence the fallback rather than trusting it.
  osascript -e 'quit app "Ollama"' >/dev/null 2>&1
  for _ in $(seq 1 8); do ollama_running || break; sleep 1; done

  if ollama_running; then
    log "AppleScript quit declined — signalling the app directly"
    pkill -TERM -f "Ollama.app/Contents/MacOS/Ollama" 2>/dev/null   # supervisor
    sleep 2
    pkill -TERM -f "Ollama.app/Contents/Resources/ollama" 2>/dev/null # server
    for _ in $(seq 1 15); do ollama_running || break; sleep 1; done
  fi

  ollama_running && die "could not stop Ollama — refusing to move files it has open"
  ok "Ollama stopped"
}

start_ollama() {
  log "starting Ollama"
  open -a Ollama >/dev/null 2>&1
  for _ in $(seq 1 30); do ollama_running && break; sleep 1; done
  ollama_running && ok "Ollama running" || warn "Ollama did not come back — start it manually"
}

# --- commands ---------------------------------------------------------------

cmd_status() {
  echo
  log "store location : $SRC"
  [ -d "$SRC" ] && log "store size     : $(du -sh "$SRC" 2>/dev/null | cut -f1)"
  log "OLLAMA_MODELS  : ${OLLAMA_MODELS:-<unset — using default>}"
  echo
  if mount | grep -q "on $DRIVE "; then
    ok "Passport mounted — $(df -h "$DRIVE" | tail -1 | awk '{print $4}') free"
    [ -d "$DEST" ] && log "destination    : $DEST ($(du -sh "$DEST" 2>/dev/null | cut -f1))"
  else
    warn "Passport NOT mounted"
  fi
  [ -f "$MARKER" ] && warn "incomplete migration recorded: $(cat "$MARKER")"
  echo
  log "internal disk  : $(df -h / | tail -1 | awk '{print $4" free ("$5" used)"}')"
}

cmd_migrate() {
  require_drive
  [ -d "$SRC" ] || die "no model store at $SRC"

  local need have
  need=$(du -sm "$SRC" | cut -f1)
  have=$(df -m "$DRIVE" | tail -1 | awk '{print $4}')
  [ "$have" -gt $((need + 5000)) ] || die "not enough space on the Passport (${need}MB needed)"

  stop_ollama
  mkdir -p "$DEST"
  echo "copying" > "$MARKER"

  log "copying $(du -sh "$SRC" | cut -f1) — this takes a while, Ctrl-C is safe"
  # FLAG SUPPORT IS PROVEN BY A REAL COPY, NOT BY --help.
  #
  # Current macOS ships `openrsync` (protocol 29), not GNU rsync. Measured:
  #     --info=progress2  -> hard error, aborted the whole 36 GB copy
  #     --progress        -> hard error
  #     --partial         -> OK
  # Grepping `--help` is not good enough: openrsync LISTS flags it then
  # rejects, so the help text said --progress was fine and the copy died.
  # Each flag is therefore tried on a throwaway directory first.
  local probe_src probe_dst RSYNC_FLAGS="-a"
  probe_src=$(mktemp -d); probe_dst=$(mktemp -d)
  echo probe > "$probe_src/f"
  for flag in --partial --info=progress2 --progress; do
    if rsync -a $flag "$probe_src"/ "$probe_dst"/ >/dev/null 2>&1; then
      RSYNC_FLAGS="$RSYNC_FLAGS $flag"
      [ "$flag" = "--info=progress2" ] && break   # supersedes --progress
    fi
  done
  rm -rf "$probe_src" "$probe_dst"
  log "rsync flags: $RSYNC_FLAGS"

  # shellcheck disable=SC2086
  if ! rsync $RSYNC_FLAGS "$SRC"/ "$DEST"/; then
    start_ollama
    die "copy failed — original store is untouched at $SRC"
  fi

  # VERIFY BEFORE DELETING. A copy that silently truncated is worse than no
  # copy at all, because the original is about to go.
  local src_n dst_n
  src_n=$(find "$SRC" -type f | wc -l | tr -d ' ')
  dst_n=$(find "$DEST" -type f | wc -l | tr -d ' ')
  if [ "$src_n" != "$dst_n" ]; then
    start_ollama
    die "file count mismatch ($src_n vs $dst_n) — original kept, nothing deleted"
  fi
  ok "verified $dst_n files"

  echo "copied" > "$MARKER"
  start_ollama
  log "original left at $SRC — remove it yourself once you are satisfied:"
  log "    rm -rf \"$SRC\""

  cat <<EOF

  Now point Ollama at the new store (persists across reboots):

      launchctl setenv OLLAMA_MODELS "$DEST"
      launchctl setenv OLLAMA_MAX_LOADED_MODELS 1

  MAX_LOADED_MODELS=1 matters: without it a model swap ADDS rather than
  evicts, and two big models will OOM this machine.

  Then restart Ollama and check: ollama list

EOF
  rm -f "$MARKER"
}

cmd_fetch() {
  require_drive
  ollama_running || die "Ollama is not running"
  echo
  for entry in "${FETCH_LIST[@]}"; do
    IFS='|' read -r model size why <<< "$entry"
    if ollama list 2>/dev/null | grep -q "${model%%:*}"; then
      ok "already present: $model"
      continue
    fi
    log "pulling $model  (~${size} GB — $why)"
    if ollama pull "$model"; then
      ok "$model"
    else
      warn "failed: $model — continuing with the rest"
    fi
    [ "$INTERRUPTED" = 1 ] && break
  done
  echo
  ok "done. Benchmark with: python3 tools/model_gate.py  (or tools/llm_profile.py)"
}

cmd_rollback() {
  [ -d "$DEST" ] || die "nothing at $DEST"
  stop_ollama
  mkdir -p "$SRC"
  log "copying back to $SRC"
  rsync -a --info=progress2 --partial "$DEST"/ "$SRC"/ || die "rollback copy failed"
  launchctl unsetenv OLLAMA_MODELS 2>/dev/null
  ok "restored — unset OLLAMA_MODELS; restart Ollama"
  start_ollama
}

case "${1:-status}" in
  status)   cmd_status ;;
  migrate)  cmd_migrate ;;
  fetch)    cmd_fetch ;;
  rollback) cmd_rollback ;;
  *) die "usage: $0 {status|migrate|fetch|rollback}" ;;
esac
