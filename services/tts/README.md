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

It is capped at 2048M in `docker-compose.yml`, which looks disproportionate for
319 MB of weights and is not. Measured on the built image (2026-08-19) while
synthesising the service's own maximum 5,000-character request: **1.38 GB
anonymous, 2.03 GB peak RSS**, against 34 MB before the first synthesis. The
same request at a 1536M cap was OOM-killed. The weights are the small part; the
torch + transformers + spacy + misaki chain around them is the rest, and none
of it is loaded by the host MLX backend. The way to spend less is to stay on
the host backend, not to trim the cap.

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

## The weights exist twice on disk, and that is not waste

An audit flagged "two duplicate Kokoro copies". They are the same model and
they are not interchangeable:

| path (host HF cache) | file | size | who loads it |
|---|---|---|---|
| `models--hexgrad--Kokoro-82M` | `kokoro-v1_0.pth` | 319 MB | `TTS_BACKEND=torch` (the `kokoro` package) |
| `models--prince-canuma--Kokoro-82M` | `kokoro-v1_0.safetensors` | 339 MB | `TTS_BACKEND=mlx` (the default, via `mlx-audio`) |

Both hold **548 tensors / 81,763,410 fp32 parameters** — verified by reading
the safetensors header and the torch checkpoint side by side — so they are the
same weights in two container formats, with different tensor-name prefixes
(`bert.module.*` in the `.pth`). They are not byte-identical, neither loader
reads the other's format, and there is no conversion step in this repo. Nothing
is reclaimable by de-duplicating them; the only way to hold one copy is to give
up one backend.

Do not "clean up" the torch copy to save the 319 MB. This is an offline
appliance: deleting it does not free a re-downloadable cache entry, it deletes
the local half of the fallback, and the fallback exists for the case where MLX
is exactly what has stopped working. 319 MB is a cheap insurance premium
against a machine that cannot speak.

## Cache

Synthesised audio is cached by `md5(text:voice)` under `TTS_CACHE_DIR`
(`data/tts_cache`). The tutor repeats a lot of stock phrasing, so the cache
earns its keep quickly. Running on the host, the default resolves to the repo's
`data/tts_cache` rather than a temp directory that is discarded on reboot.
