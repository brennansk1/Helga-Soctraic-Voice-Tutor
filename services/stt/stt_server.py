"""Helga STT service — offline speech-to-text for the Learn voice loop.

Deployment (see services/stt/README.md):
  - PRIMARY: run NATIVELY on the Apple-Silicon host (MLX/ANE acceleration), like
    Ollama. Docker containers reach it at host.docker.internal:5001.
        STT_BACKEND=nemotron-mlx python services/stt/stt_server.py
  - FALLBACK: faster-whisper (CPU, CTranslate2) — containerizable via the
    Dockerfile here.
        STT_BACKEND=faster-whisper python services/stt/stt_server.py

HTTP contract:
  POST /api/stt   body: multipart 'audio' file OR raw audio bytes
                  resp: {"text": "<transcript>", "backend": "<name>"}
  GET  /health    resp: {"status": "...", "backend": "...", "model": "..."}

The Flask routing is engine-agnostic: each backend implements
transcribe(path: str) -> str and lazily imports its own heavy deps, so the
service (and its tests) load without any model installed.
"""
import logging
import os
import tempfile

from flask import Flask, request, jsonify

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

STT_BACKEND = os.environ.get("STT_BACKEND", "nemotron-mlx")
# Default model id per backend; override with STT_MODEL.
_DEFAULT_MODELS = {
    "nemotron-mlx": "nvidia/nemotron-3.5-asr-streaming-0.6b",
    "faster-whisper": "distil-large-v3",
}
STT_MODEL = os.environ.get("STT_MODEL", _DEFAULT_MODELS.get(STT_BACKEND, ""))
# Cap upload size to avoid memory blowups on a huge POST (default 25 MB).
MAX_AUDIO_BYTES = int(os.environ.get("STT_MAX_AUDIO_BYTES", 25 * 1024 * 1024))

_backend = None


class STTBackend:
    """Interface: transcribe a local audio file path to text."""

    name = "base"

    def transcribe(self, audio_path: str) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class NemotronMLXBackend(STTBackend):
    """nvidia/nemotron-3.5-asr-streaming-0.6b via the Apple-Silicon MLX port.

    Requires the community MLX port to be installed in the host environment
    (e.g. `pip install nemotron-asr-mlx`). Import is lazy so the service starts
    and is testable without it.
    """

    name = "nemotron-mlx"

    def __init__(self, model_id: str):
        self.model_id = model_id
        self._model = None

    def _load(self):
        if self._model is None:
            # Imported lazily; the exact entrypoint depends on the chosen port.
            from nemotron_asr_mlx import load_model  # type: ignore
            logger.info(f"Loading Nemotron ASR (MLX): {self.model_id}")
            self._model = load_model(self.model_id)
        return self._model

    def transcribe(self, audio_path: str) -> str:
        model = self._load()
        result = model.transcribe(audio_path)
        # Ports return either a str or a dict with a 'text' field.
        if isinstance(result, dict):
            return (result.get("text") or "").strip()
        return str(result).strip()


class FasterWhisperBackend(STTBackend):
    """faster-whisper (CTranslate2) — CPU-friendly, containerizable fallback."""

    name = "faster-whisper"

    def __init__(self, model_id: str):
        self.model_id = model_id
        self._model = None

    def _load(self):
        if self._model is None:
            from faster_whisper import WhisperModel  # type: ignore
            compute_type = os.environ.get("STT_COMPUTE_TYPE", "int8")
            logger.info(f"Loading faster-whisper: {self.model_id} ({compute_type})")
            self._model = WhisperModel(self.model_id, device="cpu", compute_type=compute_type)
        return self._model

    def transcribe(self, audio_path: str) -> str:
        model = self._load()
        segments, _info = model.transcribe(audio_path)
        return " ".join(seg.text.strip() for seg in segments).strip()


def _build_backend(name: str) -> STTBackend:
    if name == "faster-whisper":
        return FasterWhisperBackend(STT_MODEL or "distil-large-v3")
    # default
    return NemotronMLXBackend(STT_MODEL or "nvidia/nemotron-3.5-asr-streaming-0.6b")


def get_backend() -> STTBackend:
    global _backend
    if _backend is None:
        _backend = _build_backend(STT_BACKEND)
    return _backend


def _read_audio_bytes(req) -> bytes:
    """Accept either a multipart 'audio' file or a raw audio request body."""
    if "audio" in req.files:
        return req.files["audio"].read()
    return req.get_data() or b""


@app.route("/api/stt", methods=["POST"])
def transcribe():
    audio = _read_audio_bytes(request)
    if not audio:
        return jsonify({"error": "No audio provided"}), 400
    if len(audio) > MAX_AUDIO_BYTES:
        return jsonify({"error": "Audio too large"}), 413

    # Persist to a temp file so backends can decode via their own audio stack.
    suffix = os.path.splitext(request.files["audio"].filename)[1] if "audio" in request.files else ".webm"
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix or ".webm", delete=False) as tmp:
            tmp.write(audio)
            tmp_path = tmp.name
        backend = get_backend()
        text = backend.transcribe(tmp_path)
        return jsonify({"text": text, "backend": backend.name})
    except ModuleNotFoundError as e:
        logger.error(f"STT backend deps missing: {e}")
        return jsonify({"error": f"STT backend '{STT_BACKEND}' not installed: {e}"}), 503
    except Exception as e:
        logger.error(f"STT transcription failed: {e}", exc_info=True)
        return jsonify({"error": "Transcription failed"}), 500
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "backend": STT_BACKEND, "model": STT_MODEL})


if __name__ == "__main__":
    port = int(os.environ.get("STT_PORT", 5001))
    logger.info(f"Starting STT service (backend={STT_BACKEND}, model={STT_MODEL}) on port {port}")
    app.run(host="0.0.0.0", port=port)
