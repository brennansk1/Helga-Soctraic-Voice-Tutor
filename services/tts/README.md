# Helga TTS service (Kokoro-82M)

```
POST /api/tts   body: {"text": "...", "voice": "af_heart"}
                resp: audio/wav
GET  /health    -> {"backend_configured": ..., "backend_active": ...}
```

## Backends

| `TTS_BACKEND` | Runtime | Where it runs | Notes |
|---|---|---|---|
| `mlx` (default) | `mlx-audio` | **Native host (Apple Silicon)** | Same Kokoro-82M model, same voices. No PyTorch. Needs Metal, which a Linux container on macOS does not have. |
| `torch` | `kokoro` | Container **or** host | Portable fallback. Pulls the PyTorch chain. |

This is a runtime swap, not a model swap — identical voices and quality.

## Recommended deployment — native host (mirrors Ollama and STT)

```bash
pip install -r services/tts/requirements-host.txt
TTS_BACKEND=mlx TTS_PORT=5005 python3 services/tts/tts_server.py
```

Or start it alongside Ollama and STT with `scripts/host_services.sh start`.

The Docker stack reaches it at `host.docker.internal:5005`, which is the
default `TTS_URL` in `docker-compose.yml` — the same shape as Ollama at `:11434`
and STT at `:5001`.

## Fallback — the container

Behind a compose profile, so `docker compose up` neither builds nor starts it:

```bash
docker compose --profile portable up -d tts
TTS_URL=http://helga-tts:5005 docker compose up -d web-ui core-logic
```

> **Why the container uses torch and not MLX.** MLX publishes a manylinux
> aarch64 wheel, so `pip install mlx-audio` succeeds inside a Linux image and
> the service looks configured — but there is no Metal in that VM. This is
> exactly how it was set up before: `requirements.txt` declared `mlx-audio`
> with `kokoro` commented out, so the container had **no working backend at
> all**. The health check passed (it does not synthesise anything) and the
> first real request raised `no TTS backend available`. `/health` now reports
> `backend_active`, which is `None` until the first synthesis — so a green
> health check still is not proof the backend loads.

## Cache

Synthesised audio is cached by `md5(text:voice)` under `TTS_CACHE_DIR`
(`data/tts_cache`). The tutor repeats a lot of stock phrasing, so the cache
earns its keep quickly. Running on the host, the default resolves to the repo's
`data/tts_cache` rather than a temp directory that is discarded on reboot.
