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
MODEL="${OLLAMA_MODEL:-nail-35b-a3b}"
echo "[2/6] Checking $MODEL..."
# The project model is imported from a local GGUF, not pulled from a registry
# (see docs/MODEL.md). `ollama pull` on it fails, so only pull when the model
# is genuinely absent AND looks like a registry name.
if ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL"; then
    echo "  $MODEL already installed (local import — not pulling)"
elif ollama list 2>/dev/null | awk '{print $1}' | grep -qx "$MODEL:latest"; then
    echo "  $MODEL:latest already installed (local import — not pulling)"
else
    echo "  $MODEL not installed."
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
echo "  Ollama:  http://localhost:11434"
echo "  Host:    scripts/host_services.sh status"
echo "  Logs:    docker compose logs -f   |   logs/host-*.log"
