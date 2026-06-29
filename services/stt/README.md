# Helga STT service (offline speech-to-text)

Provides the Learn voice loop. Engine-agnostic HTTP contract:

```
POST /api/stt   body: multipart 'audio' file OR raw audio bytes
                resp: {"text": "<transcript>", "backend": "<name>"}
GET  /health
```

## Backends

| `STT_BACKEND` | Engine | Where it runs | Notes |
|---|---|---|---|
| `nemotron-mlx` (default) | `nvidia/nemotron-3.5-asr-streaming-0.6b` via the MLX port | **Native host (Apple Silicon)** | Cache-aware streaming, sub-100ms, 40 languages, ANE-accelerated. MLX/ANE **cannot** run in a Linux container. |
| `faster-whisper` | faster-whisper (CTranslate2) | Container **or** host | CPU, batch; portable fallback. Needs `ffmpeg`. |

## Recommended deployment — native host (mirrors Ollama)

The fast path runs natively on the Mac and the Docker stack reaches it via
`host.docker.internal:5001`, exactly like Ollama at `:11434`.

```bash
# one-time (host venv): install MLX port + flask
pip install flask nemotron-asr-mlx     # community MLX port — verify package name/maturity

# run it
STT_BACKEND=nemotron-mlx STT_PORT=5001 python services/stt/stt_server.py
```

Then set `STT_URL=http://host.docker.internal:5001` for the web-ui service
(already defaulted in docker-compose.yml).

> Caveats: the MLX/CoreML ports are community (FluidInference / 199-biotechnologies),
> not official NVIDIA — verify the package, maturity, and the NVIDIA model license
> before production use. Benchmark WER + latency on your M4.

## Fallback — containerized faster-whisper

If you'd rather keep everything in Docker (CPU, higher latency, no ANE), enable
the commented `stt` service in `docker-compose.yml` (it builds this Dockerfile
with `STT_BACKEND=faster-whisper`) and point `STT_URL` at `http://helga-stt:5001`.

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `STT_BACKEND` | `nemotron-mlx` | `nemotron-mlx` \| `faster-whisper` |
| `STT_MODEL` | per-backend default | model id / path |
| `STT_PORT` | `5001` | listen port |
| `STT_COMPUTE_TYPE` | `int8` | faster-whisper compute type |
| `STT_MAX_AUDIO_BYTES` | `26214400` | max upload size (25 MB) |
