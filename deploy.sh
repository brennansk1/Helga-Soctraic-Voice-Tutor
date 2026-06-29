#!/bin/bash
# Helga Socratic Tutor — Mac Mini Deployment

set -e

echo "=== Helga Socratic Tutor — Mac Mini Deployment ==="

# 1. Prerequisites check
echo "[1/5] Checking prerequisites..."
command -v docker >/dev/null 2>&1 || { echo "Docker required. Install Docker Desktop for Mac."; exit 1; }
command -v ollama >/dev/null 2>&1 || { echo "Ollama required. Install from ollama.com"; exit 1; }

# 2. Pull LLM model
echo "[2/5] Pulling Qwen 3 14B model..."
ollama pull qwen3.5:9b

# 3. Create data directories
echo "[3/5] Setting up data directories..."
mkdir -p data/logs data/sqlite data/tts_cache data/hf_cache data/courses data/uploads

# 4. Environment setup
echo "[4/5] Checking environment..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from template. Review before continuing."
fi

# 5. Build and start
echo "[5/5] Building and starting services..."
docker compose build
docker compose up -d

# Health check
echo "Waiting for services to start..."
sleep 10
for service in web-ui core-logic rag-engine tts; do
    if docker inspect --format='{{.State.Health.Status}}' "helga-$service" 2>/dev/null | grep -q healthy; then
        echo "  + $service: healthy"
    else
        echo "  - $service: not yet healthy (may still be starting)"
    fi
done

echo ""
echo "=== Helga is running ==="
echo "  Web UI:  http://localhost:5050"
echo "  Ollama:  http://localhost:11434"
echo "  Logs:    docker compose logs -f"
