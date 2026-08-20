import hashlib
import logging
import os
import io
import tempfile

import numpy as np
import soundfile as sf
from flask import Flask, request, send_file, jsonify

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Env-overridable; fall back to a temp dir so the module imports/runs outside the
# container (and is testable) rather than crashing on a read-only /app at import.
# /app/data/tts_cache is the container path. Running on the host, that does
# not exist, and the except-branch below used to drop the cache into a temp dir
# — which works but is thrown away on every reboot, so every phrase the tutor
# has ever spoken gets re-synthesised. Prefer the repo's data directory when it
# is reachable.
_DEFAULT_CACHE = "/app/data/tts_cache"
if not os.path.isdir("/app"):
    _DEFAULT_CACHE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "tts_cache")
CACHE_DIR = os.environ.get("TTS_CACHE_DIR", _DEFAULT_CACHE)
try:
    os.makedirs(CACHE_DIR, exist_ok=True)
except (PermissionError, OSError):
    CACHE_DIR = os.path.join(tempfile.gettempdir(), "helga_tts_cache")
    os.makedirs(CACHE_DIR, exist_ok=True)

pipeline = None

VOICES = [
    "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
    "am_adam", "am_michael", "bf_emma", "bf_isabella",
    "bm_george", "bm_lewis", "af_alloy", "af_nova", "am_echo"
]


# Apple-native-first: the SAME Kokoro-82M model, run on MLX instead of
# PyTorch. This is a runtime swap, not a model swap — identical voices and
# quality — but it drops the entire torch dependency chain from this service,
# which matters on a 24 GB box measured already swapping 10.3/11.3 GB.
#
#   mlx     -> mlx-audio (Apple Silicon, default; prince-canuma/Kokoro-82M)
#   torch   -> the original `kokoro` package (portable fallback)
#
# Import is lazy and the backend falls back automatically, so a machine
# without MLX still works. See docs/SPRINT_PLAN.md "Apple-native first".
TTS_BACKEND = os.environ.get("TTS_BACKEND", "mlx").lower()
MLX_KOKORO_REPO = os.environ.get("MLX_KOKORO_REPO", "prince-canuma/Kokoro-82M")
# The torch path loads the upstream weights; 'a' is American English, which is
# what every voice in VOICES below is trained for.
TORCH_KOKORO_REPO = os.environ.get("KOKORO_REPO", "hexgrad/Kokoro-82M")
KOKORO_LANG = os.environ.get("KOKORO_LANG", "a")

_backend_in_use = None


class _MlxKokoro:
    """Adapter presenting the same call shape as KPipeline."""

    def __init__(self, repo):
        from mlx_audio.tts.utils import load_model
        self._model = load_model(repo)

    def __call__(self, text, voice="af_heart", speed=1.0):
        # mlx-audio yields segment objects carrying `.audio`; normalise to the
        # (graphemes, phonemes, audio) triple the caller already unpacks.
        for seg in self._model.generate(text=text, voice=voice, speed=speed):
            audio = getattr(seg, "audio", seg)
            yield (None, None, audio)


def get_pipeline():
    global pipeline, _backend_in_use
    if pipeline is not None:
        return pipeline

    errors = []
    order = ["mlx", "torch"] if TTS_BACKEND == "mlx" else ["torch", "mlx"]
    for backend in order:
        try:
            if backend == "mlx":
                pipeline = _MlxKokoro(MLX_KOKORO_REPO)
            else:
                from kokoro import KPipeline
                # repo_id explicitly: upstream's documented call passes it, and
                # leaving it implicit means the weights this service loads are
                # whatever the installed version happens to default to. On an
                # offline appliance the model identity should be stated, not
                # inherited.
                pipeline = KPipeline(lang_code=KOKORO_LANG,
                                     repo_id=TORCH_KOKORO_REPO)
            _backend_in_use = backend
            logger.info(f"Kokoro TTS initialized (backend={backend})")
            return pipeline
        except Exception as e:
            errors.append(f"{backend}: {e}")
            logger.warning(f"TTS backend '{backend}' unavailable: {e}")

    raise RuntimeError("no TTS backend available — " + "; ".join(errors))


def active_backend():
    """Which backend actually loaded. Exposed so /health can report it rather
    than leaving an operator guessing whether the MLX path is really in use."""
    return _backend_in_use


@app.route('/api/tts', methods=['POST'])
def synthesize():
    data = request.get_json()
    if not data or not data.get('text'):
        return jsonify({"error": "Missing 'text' field"}), 400

    text = data['text'][:5000]  # Limit text length
    voice = data.get('voice', 'af_heart')

    if voice not in VOICES:
        voice = 'af_heart'

    cache_key = hashlib.md5(f"{text}:{voice}".encode()).hexdigest()
    cache_path = os.path.join(CACHE_DIR, f"{cache_key}.wav")

    if os.path.exists(cache_path):
        logger.info(f"Cache hit for TTS: {cache_key[:8]}")
        return send_file(cache_path, mimetype='audio/wav')

    try:
        pipe = get_pipeline()
        audio_chunks = []
        for _, _, chunk in pipe(text, voice=voice):
            audio_chunks.append(chunk)

        if not audio_chunks:
            return jsonify({"error": "No audio generated"}), 500

        full_audio = np.concatenate(audio_chunks)
        sf.write(cache_path, full_audio, 24000)
        logger.info(f"Generated TTS audio: {cache_key[:8]} ({len(full_audio)} samples)")
        return send_file(cache_path, mimetype='audio/wav')

    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        return jsonify({"error": "TTS synthesis failed"}), 500


@app.route('/api/voices', methods=['GET'])
def list_voices():
    return jsonify({"voices": VOICES})


@app.route('/health', methods=['GET'])
def health():
    # Report the backend that ACTUALLY loaded, not the configured preference —
    # a silent fallback from mlx to torch is a real memory regression an
    # operator needs to be able to see.
    return jsonify({
        "status": "healthy",
        "engine": "kokoro",
        "params": "82M",
        "backend_configured": TTS_BACKEND,
        "backend_active": active_backend(),  # None until first synthesis
    })


if __name__ == '__main__':
    # Port is env-driven because this service now runs in two places: natively
    # on the Apple-Silicon host (the default — MLX needs Metal, which a Linux
    # container on macOS does not have) and in the portable fallback container.
    # STT already worked this way; TTS had the port hardcoded, so the host
    # deployment had no way to move off a clash.
    port = int(os.environ.get("TTS_PORT", "5005"))
    logger.info(f"Starting Kokoro TTS service on port {port} "
                f"(backend={TTS_BACKEND})")
    app.run(host='0.0.0.0', port=port)
