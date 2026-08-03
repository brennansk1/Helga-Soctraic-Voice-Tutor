#!/usr/bin/env python3
"""
Helga Socratic Tutor — System Startup Script (Mac Mini)

Orchestrates:
1. Verify prerequisites (Docker, Ollama, model)
2. Build Docker images
3. Start all services via docker compose
4. Verify system health
"""

import subprocess
import sys
import os
import time
import requests
import logging
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
LOGS_DIR = PROJECT_ROOT / "data" / "logs"

SERVICES = {
    "web-ui": {
        "port": 5050,
        "health_url": "http://localhost:5050/health",
        "description": "Flask Web UI",
    },
    "core-logic": {
        "port": 5003,
        "health_url": "http://localhost:5003/health",
        "description": "FSM Core Logic",
    },
    "rag-engine": {
        "port": 5002,
        "health_url": "http://localhost:5002/health",
        "description": "RAG Engine (SQLite + Embeddings)",
    },
    "tts": {
        "port": 5005,
        "health_url": "http://localhost:5005/health",
        "description": "Kokoro TTS Engine",
    },
}

OLLAMA_URL = "http://localhost:11434"
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


def model_is_available(wanted, installed):
    """Is `wanted` actually servable? Returns (ok, closest_near_miss).

    The old check was `any(wanted in m for m in installed)` — a SUBSTRING test,
    which reports a green preflight for a model Ollama cannot serve. Ask for
    'qwen3:14b' with only 'qwen3:14b-q4_K_M' pulled and it passed, then every
    generation call 404'd. A preflight that passes when the system is broken is
    worse than no preflight: it sends you looking somewhere else.

    Ollama resolves a bare name to its ':latest' tag, so that one alias is real
    and is honoured. Nothing else is.
    """
    installed = [m for m in (installed or []) if m]
    candidates = {wanted} | ({f"{wanted}:latest"} if ":" not in wanted else set())
    if candidates & set(installed):
        return True, None
    # Surface the near-miss: "not found" alongside a list containing something
    # that looks identical is how this gets misread as a display quirk.
    #
    # Rank it, do not just prefix-match. A bare startswith on the base name
    # crosses version boundaries — 'qwen3' matches 'qwen3.5:9b', so asking for
    # 'qwen3:14b' pointed at a 9b of a different generation while the actual
    # near miss, 'qwen3:14b-q4_K_M', sat further down the list.
    base = wanted.split(":")[0]
    near = next((m for m in installed if m.startswith(wanted)), None)
    if not near:
        near = next((m for m in installed if m.split(":")[0] == base), None)
    return False, near


def check_prerequisites():
    """Verify Docker, Ollama, and model are available."""
    all_ok = True

    # Docker
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            logger.info(f"Docker Compose: {result.stdout.strip()}")
        else:
            logger.error("Docker Compose not found")
            all_ok = False
    except FileNotFoundError:
        logger.error("Docker not installed")
        all_ok = False

    # Ollama
    try:
        resp = requests.get(f"{OLLAMA_URL}/api/tags", timeout=5)
        if resp.ok:
            models = [m.get("name", "") for m in resp.json().get("models", [])]
            ok, near = model_is_available(OLLAMA_MODEL, models)
            if ok:
                logger.info(f"Ollama model '{OLLAMA_MODEL}' is available")
            else:
                logger.warning(f"Model '{OLLAMA_MODEL}' not found. Available: {models}")
                if near:
                    logger.warning(
                        f"Closest installed tag is '{near}'. That is a DIFFERENT "
                        f"model as far as the API is concerned — requests for "
                        f"'{OLLAMA_MODEL}' will 404. Set OLLAMA_MODEL='{near}' "
                        f"or pull the exact tag."
                    )
                logger.info(f"Pulling model... run: ollama pull {OLLAMA_MODEL}")
                all_ok = False
        else:
            logger.error(f"Ollama returned status {resp.status_code}")
            all_ok = False
    except requests.exceptions.ConnectionError:
        logger.error("Ollama not running at localhost:11434. Start it with: ollama serve")
        all_ok = False

    return all_ok


def build_services():
    """Build Docker images."""
    logger.info("Building Docker images...")
    result = subprocess.run(
        ["docker", "compose", "build"],
        cwd=str(PROJECT_ROOT),
        timeout=600
    )
    return result.returncode == 0


def start_services():
    """Start services via docker compose."""
    logger.info("Starting services...")
    result = subprocess.run(
        ["docker", "compose", "up", "-d"],
        cwd=str(PROJECT_ROOT),
        timeout=120
    )
    return result.returncode == 0


def check_health(timeout=60):
    """Poll all services for health."""
    logger.info(f"Waiting up to {timeout}s for services to become healthy...")
    start = time.time()
    healthy = set()

    while time.time() - start < timeout:
        for name, svc in SERVICES.items():
            if name in healthy:
                continue
            try:
                resp = requests.get(svc["health_url"], timeout=3)
                if resp.ok:
                    healthy.add(name)
                    logger.info(f"  + {name}: healthy")
            except Exception:
                pass

        if len(healthy) == len(SERVICES):
            return True
        time.sleep(2)

    for name in SERVICES:
        if name not in healthy:
            logger.warning(f"  - {name}: NOT healthy after {timeout}s")
    return False


def main():
    logger.info("=" * 50)
    logger.info("Helga Socratic Tutor — Startup")
    logger.info("=" * 50)

    # Ensure data directories
    os.makedirs(LOGS_DIR, exist_ok=True)
    for d in ["courses", "uploads", "tts_cache", "hf_cache", "sqlite"]:
        os.makedirs(PROJECT_ROOT / "data" / d, exist_ok=True)

    if not check_prerequisites():
        logger.error("Prerequisites not met. Fix the above issues and retry.")
        sys.exit(1)

    if not build_services():
        logger.error("Docker build failed.")
        sys.exit(1)

    if not start_services():
        logger.error("Docker start failed.")
        sys.exit(1)

    all_healthy = check_health(timeout=90)

    logger.info("")
    logger.info("=" * 50)
    if all_healthy:
        logger.info("All services healthy!")
    else:
        logger.warning("Some services not yet healthy (may still be starting)")
    logger.info(f"Web UI: http://localhost:5050")
    logger.info(f"Ollama: {OLLAMA_URL}")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()
