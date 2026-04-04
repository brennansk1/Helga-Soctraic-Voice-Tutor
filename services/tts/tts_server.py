import hashlib
import logging
import os
import io

import numpy as np
import soundfile as sf
from flask import Flask, request, send_file, jsonify

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)

CACHE_DIR = "/app/data/tts_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

pipeline = None

VOICES = [
    "af_heart", "af_bella", "af_nicole", "af_sarah", "af_sky",
    "am_adam", "am_michael", "bf_emma", "bf_isabella",
    "bm_george", "bm_lewis", "af_alloy", "af_nova", "am_echo"
]


def get_pipeline():
    global pipeline
    if pipeline is None:
        try:
            from kokoro import KPipeline
            pipeline = KPipeline(lang_code='a')
            logger.info("Kokoro TTS pipeline initialized")
        except Exception as e:
            logger.error(f"Failed to initialize Kokoro pipeline: {e}")
            raise
    return pipeline


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
    return jsonify({"status": "healthy", "engine": "kokoro", "params": "82M"})


if __name__ == '__main__':
    logger.info("Starting Kokoro TTS service on port 5005")
    app.run(host='0.0.0.0', port=5005)
