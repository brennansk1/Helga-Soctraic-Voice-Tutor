#!/usr/bin/env bash
# Restart a Helga container, refusing while a build is in flight.
#
# WHY THIS EXISTS
# ---------------
# Hydration runs in a thread inside helga-rag-engine (a resume) or
# helga-core-logic (a fresh build). `docker restart` kills that thread with no
# warning and no error: the concepts already written survive on disk, the
# course keeps status "building", and the work simply stops. Nothing tells you.
#
# It cost three builds in one session — twice while verifying an unrelated fix,
# once bundled into a one-line command that also restarted web-ui. Each loss was
# an hour or more of model time on this machine.
#
#   tools/safe_restart.sh helga-rag-engine          # refuses if a build is live
#   tools/safe_restart.sh --force helga-rag-engine  # you have decided
#
# web-ui is always safe: it holds no build thread.
set -euo pipefail

FORCE=0
case "${1:-}" in
    --force) FORCE=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
esac
if [ $# -eq 0 ]; then
    echo "usage: $0 [--force] <container> [container...]" >&2
    exit 2
fi
for c in "$@"; do
    case "$c" in
        -*) echo "unknown option: $c" >&2; exit 2 ;;
    esac
done

holds_a_build() {
    case "$1" in
        helga-rag-engine|helga-core-logic) return 0 ;;
        *) return 1 ;;
    esac
}

build_is_live() {
    # build_state is the durable record and it is authoritative ONLY when the
    # heartbeat is fresh; a stale record means the owner already died.
    python3 - <<'PY' 2>/dev/null || return 1
import json, sys, time
try:
    d = json.load(open("data/build_state.json"))
except Exception:
    sys.exit(1)
if not d.get("active"):
    sys.exit(1)
# A LIVE HYDRATION HEARTBEATS EVERY FEW SECONDS -- measured at 2-10s while
# writing concepts -- so three minutes of silence already means the owner is
# gone. The 20-minute figure used elsewhere is the REAPER's budget, chosen so a
# slow model is never mistaken for a corpse; borrowing it here only produced
# false refusals for twenty minutes after every death.
if time.time() - (d.get("updated_at") or 0) > 180:
    sys.exit(1)          # stale: the owner is gone, restarting costs nothing
print("%s (stage %s, heartbeat %ds ago)" % (
    d.get("topic") or d.get("course_uid"), d.get("stage"),
    int(time.time() - (d.get("updated_at") or 0))))
PY
}

for c in "$@"; do
    if [ "$FORCE" -eq 0 ] && holds_a_build "$c"; then
        if live=$(build_is_live); then
            echo "REFUSING to restart $c — a build is running: $live" >&2
            echo "  It runs in a thread inside that container and will be killed." >&2
            echo "  Wait for it, or re-run with --force if you accept losing it." >&2
            exit 1
        fi
    fi
done

for c in "$@"; do
    echo "restarting $c"
    docker restart "$c" >/dev/null
done
echo "done"
