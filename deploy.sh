#!/bin/bash
# Helga Socratic Tutor — Mac Mini deployment
#
# v1 targets macOS on Apple Silicon natively (docs/SPRINT_PLAN.md). Three
# services run on the HOST rather than in a container, because they need the
# GPU or the Neural Engine and a Linux container on macOS has neither:
#
#     Ollama   :11434   inference
#     TTS      :5005    Kokoro-82M on MLX
#     STT      :5001    Nemotron-3.5-ASR on MLX/ANE
#
# This script used to check that the `ollama` binary existed, pull a model, and
# run `docker compose up`. It started none of the host services and set none of
# the Ollama environment that decides whether the stack feels fast. A user
# following the README got a healthy container stack with no voice in either
# direction and no error explaining why.

set -e
cd "$(dirname "$0")"

echo "=== Helga Socratic Tutor — Mac deployment ==="

# 1. Prerequisites
echo "[1/6] Checking prerequisites..."
command -v docker >/dev/null 2>&1 || { echo "Docker required. Install Docker Desktop for Mac."; exit 1; }
command -v ollama >/dev/null 2>&1 || { echo "Ollama required. Install from ollama.com"; exit 1; }
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" != "arm64" ]; then
    echo "  ! Not Apple Silicon — the MLX voice services will not run here."
fi

# 2. Model
#
# THE -ctx SUFFIX IS NOT COSMETIC. This script used to check for
# `nail-35b-a3b`, while docker-compose and the code defaults both ask for
# `nail-35b-a3b-ctx`. Following the documented install therefore left you with
# a model the stack never requests -- and if you did point the stack at the
# base tag, its 4096-token context returns
#
#     400 — request (4212 tokens) exceeds the available context size
#
# for FIVE OF SIX MODULES IN EVERY BUILD. The builder reads that as an empty
# result and falls back, so nothing surfaces as an error: the course is simply
# a third shorter than it should be. See docs/MODEL.md.
#
# So: target the -ctx tag, and build it from the base if the base is present.
# `ollama create` reuses the existing blob -- no re-download.
MODEL="${OLLAMA_MODEL:-nail-35b-a3b-ctx}"
BASE_MODEL="${MODEL%-ctx}"
echo "[2/6] Checking $MODEL..."

have() { ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$1" \
         || ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$1:latest"; }

if have "$MODEL"; then
    echo "  $MODEL already installed"
elif [ "$BASE_MODEL" != "$MODEL" ] && have "$BASE_MODEL"; then
    echo "  $BASE_MODEL is installed but $MODEL is not."
    echo "  Creating $MODEL from it (reuses the blob, no re-download)..."
    printf 'FROM %s\nPARAMETER num_ctx 16384\n' "$BASE_MODEL" > /tmp/Helga.Modelfile.ctx
    ollama create "$MODEL" -f /tmp/Helga.Modelfile.ctx || {
        echo "  ! Could not create $MODEL. See docs/MODEL.md."
        exit 1
    }
    rm -f /tmp/Helga.Modelfile.ctx
    echo "  + $MODEL created"
else
    # The project model is imported from a local GGUF, not pulled from a
    # registry (docs/MODEL.md), so `ollama pull` fails on it. Only try a pull
    # in case this is some other, published tag.
    echo "  $MODEL not installed, and neither is $BASE_MODEL."
    echo "  If this is the project model, import it: see docs/MODEL.md"
    echo "  Attempting a registry pull in case it is a published tag..."
    ollama pull "$MODEL" || {
        echo "  ! Pull failed. Import the GGUF per docs/MODEL.md, then re-run."
        exit 1
    }
fi

# 3. Data directories
echo "[3/6] Setting up data directories..."
mkdir -p data/logs data/sqlite data/tts_cache data/hf_cache data/courses data/uploads logs

# 4. Environment
echo "[4/6] Checking environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "  Created .env from template. Review before continuing."
fi

# 5. Host-native services + the Ollama settings that matter
echo "[5/6] Starting host services (Ollama, TTS, STT)..."
scripts/host_services.sh start

# 6. Containers
echo "[6/6] Building and starting containers..."
docker compose build
docker compose up -d

echo "Waiting for services to start..."
sleep 10
# `tts` is deliberately absent: it runs on the host now and has no container to
# inspect unless you opted into `--profile portable`.
for service in web-ui core-logic rag-engine research; do
    if docker inspect --format='{{.State.Health.Status}}' "helga-$service" 2>/dev/null | grep -q healthy; then
        echo "  + $service: healthy"
    else
        echo "  - $service: not yet healthy (may still be starting)"
    fi
done

echo ""
echo "=== Preflight ==="
# Non-fatal: reports what is misconfigured on the host, which is where most of
# the latency decisions live and none of it is visible from inside a container.
python3 scripts/mac_preflight.py || true

echo ""
echo "=== Helga is running ==="
echo "  Web UI:  http://localhost:5050"
# The one page that turns "something is wrong" into "run this command".
echo "  Setup:   http://localhost:5050/setup   <- if anything above looked wrong"
echo "  Ollama:  http://localhost:11434"
echo "  Host:    scripts/host_services.sh status"
echo "  Logs:    docker compose logs -f   |   logs/host-*.log"
