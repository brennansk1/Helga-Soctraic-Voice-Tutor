#!/bin/bash
# host_services.sh — start the services that must NOT run in a container.
#
# WHY THIS EXISTS
#
# Three of Helga's services need the Apple GPU or the Neural Engine, and a
# Linux container on macOS has neither — Docker Desktop runs a VM with no Metal
# passthrough. So they run natively on the host and the Docker stack reaches
# them through host.docker.internal:
#
#     Ollama   :11434   inference
#     STT      :5001    Nemotron-3.5-ASR via MLX/ANE
#     TTS      :5005    Kokoro-82M via MLX
#
# That topology was already the documented plan (docs/SPRINT_PLAN.md,
# "Apple-native first") and deploy.sh started none of it: it checked that the
# `ollama` binary existed, pulled a model, and ran `docker compose up`. A user
# following the README got a stack with no voice in either direction and no
# error saying why — the containers were healthy, the host processes simply
# were not running.
#
# USAGE
#     scripts/host_services.sh start     # start whatever is not already up
#     scripts/host_services.sh stop
#     scripts/host_services.sh status
#
# Logs go to logs/host-*.log. Safe to re-run: anything already listening is
# left alone.

set -uo pipefail
cd "$(dirname "$0")/.." || exit 1

STT_PORT="${STT_PORT:-5001}"
TTS_PORT="${TTS_PORT:-5005}"
OLLAMA_PORT="${OLLAMA_PORT:-11434}"
PIDS=".host_pids"
mkdir -p logs

listening() { nc -z 127.0.0.1 "$1" >/dev/null 2>&1; }

# ── Ollama host settings ─────────────────────────────────────────────────────
# These are worth more than any tuning inside this repo, and every one of them
# is invisible from the container side. See docs/PERFORMANCE_PASS.md.
apply_ollama_env() {
    command -v launchctl >/dev/null 2>&1 || return 0
    # 30m, not -1: the weights are ~12.7 GB against a ~15.0 GB safe ceiling, so
    # holding them through the hours nobody is studying is most of the box's
    # headroom spent on nothing. 30m still outlasts any pause inside a session,
    # which is the only pause a reload (~133s) would be felt in. Override by
    # exporting OLLAMA_KEEP_ALIVE before calling this script.
    launchctl setenv OLLAMA_KEEP_ALIVE "${OLLAMA_KEEP_ALIVE:-30m}"
    launchctl setenv OLLAMA_NUM_PARALLEL "${OLLAMA_NUM_PARALLEL:-4}"
    launchctl setenv OLLAMA_MAX_LOADED_MODELS "${OLLAMA_MAX_LOADED_MODELS:-1}"
    launchctl setenv OLLAMA_CONTEXT_LENGTH "${OLLAMA_CONTEXT_LENGTH:-8192}"
    echo "  Ollama env set (keep_alive=${OLLAMA_KEEP_ALIVE:-30m}, "\
"parallel=${OLLAMA_NUM_PARALLEL:-4}, ctx=${OLLAMA_CONTEXT_LENGTH:-8192})"
}

start_one() {
    local name="$1" port="$2"; shift 2
    if listening "$port"; then
        echo "  $name already listening on :$port"
        return 0
    fi
    echo "  starting $name on :$port"
    "$@" >> "logs/host-$name.log" 2>&1 &
    echo "$name $!" >> "$PIDS"
}

case "${1:-start}" in
start)
    echo "Host services (these cannot run in a container on macOS):"
    : > "$PIDS"
    apply_ollama_env

    if ! listening "$OLLAMA_PORT"; then
        # A launchctl setenv only reaches a process started AFTER it, so an
        # Ollama that is already running keeps whatever it was launched with.
        # Starting it here means the settings above actually take effect.
        start_one ollama "$OLLAMA_PORT" ollama serve
        for _ in $(seq 1 30); do listening "$OLLAMA_PORT" && break; sleep 1; done
    else
        echo "  ollama already listening on :$OLLAMA_PORT"
        echo "  (note: env above applies to processes started after it — restart"
        echo "   ollama to pick up keep-alive and context changes)"
    fi

    start_one tts "$TTS_PORT" env TTS_BACKEND="${TTS_BACKEND:-mlx}" \
        TTS_PORT="$TTS_PORT" python3 services/tts/tts_server.py
    start_one stt "$STT_PORT" env STT_BACKEND="${STT_BACKEND:-nemotron-mlx}" \
        STT_PORT="$STT_PORT" python3 services/stt/stt_server.py

    echo
    echo "Then: docker compose up -d"
    echo "Check: scripts/host_services.sh status"
    ;;

stop)
    [ -f "$PIDS" ] || { echo "nothing recorded in $PIDS"; exit 0; }
    while read -r name pid; do
        if kill "$pid" 2>/dev/null; then echo "  stopped $name ($pid)"; fi
    done < "$PIDS"
    rm -f "$PIDS"
    ;;

status)
    fail=0
    for pair in "ollama $OLLAMA_PORT" "stt $STT_PORT" "tts $TTS_PORT"; do
        set -- $pair
        if listening "$2"; then
            printf "  %-8s up    :%s\n" "$1" "$2"
        else
            printf "  %-8s DOWN  :%s\n" "$1" "$2"; fail=1
        fi
    done
    exit $fail
    ;;

*)
    echo "usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
