FROM python:3.11-slim

WORKDIR /app

# ffmpeg is needed by faster-whisper to decode browser audio (webm/opus, wav).
RUN apt-get update && apt-get install -y \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY stt_server.py .

EXPOSE 5001

# Containers can only run the CPU faster-whisper backend (no MLX/ANE in Linux).
ENV STT_BACKEND=faster-whisper

CMD ["python", "stt_server.py"]
