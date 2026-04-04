FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    portaudio19-dev \
    libsndfile1 \
    cmake \
    pkg-config \
    libavformat-dev \
    libavcodec-dev \
    libavdevice-dev \
    libavutil-dev \
    libswscale-dev \
    libswresample-dev \
    libavfilter-dev \
    libopus-dev \
    libvpx-dev \
    ffmpeg \
    libzim-dev \
    liblzma-dev \
    libicu-dev \
    zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

# Install torch cpu first to save bandwidth
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# Copy requirements
COPY services/core/requirements.txt /app/services/core/requirements.txt
COPY services/rag/requirements.txt /app/services/rag/requirements.txt
COPY services/audio/requirements.txt /app/services/audio/requirements.txt
COPY services/input/requirements.txt /app/services/input/requirements.txt

# Install dependencies
RUN pip install --upgrade pip
RUN pip install --no-cache-dir --default-timeout=1000 -r services/core/requirements.txt
RUN pip install --no-cache-dir --default-timeout=1000 -r services/rag/requirements.txt
RUN pip install --no-cache-dir --default-timeout=1000 -r services/audio/requirements.txt
RUN pip install --no-cache-dir --default-timeout=1000 -r services/input/requirements.txt

# Copy source code
COPY . /app

# Set python path
ENV PYTHONPATH=/app/services/core:/app/services/rag:/app/services/audio:/app/services/input

CMD ["python", "services/core/fsm_logic.py"]
