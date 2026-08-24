FROM python:3.11-slim

WORKDIR /app

# ffmpeg is needed by faster-whisper to decode browser audio (webm/opus, wav).
RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
# The faster-whisper backend is installed HERE, not in requirements.txt.
#
# requirements.txt deliberately leaves it commented out so the default build
# does not pull CTranslate2 for a path v1 does not take (Apple-native-first —
# the primary backend is nemotron-mlx on the host). That decision is about the
# DEFAULT path, and this image is not the default path: the service is gated
# behind the `portable` profile, whose entire purpose is the containerised
# fallback.
#
# Without this the container starts, answers /health, and fails every
# transcription with "No module named 'faster_whisper'" — a fallback that
# exists and cannot fall back.
ARG STT_INSTALL_FALLBACK=1
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    if [ "$STT_INSTALL_FALLBACK" = "1" ]; then pip install --no-cache-dir faster-whisper==1.0.3 requests; fi

COPY stt_server.py .

EXPOSE 5001

# Containers can only run the CPU faster-whisper backend (no MLX/ANE in Linux).
ENV STT_BACKEND=faster-whisper

CMD ["python", "stt_server.py"]
