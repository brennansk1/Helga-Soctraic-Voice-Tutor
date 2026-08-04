FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/core/requirements.txt .
# No PyTorch here. It used to be installed explicitly with the CPU index URL,
# which contradicted this service's own requirements.txt — that file removed
# sentence-transformers precisely to drop the torch chain, and nothing in
# services/core or services/common imports torch. The line was pulling a
# multi-hundred-MB wheel into the image, and its build time, for nothing.
#
# Under the Apple-native-first decision (docs/SPRINT_PLAN.md) it would be the
# wrong dependency even if something did need it: a container on macOS has no
# Metal, so torch there runs on virtualised CPU. Anything needing the GPU runs
# on the host — Ollama, STT and TTS all do.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY services/core /app/services/core
COPY services/common /app/services/common

ENV PYTHONPATH=/app

WORKDIR /app/services/core

EXPOSE 5003

CMD ["python", "fsm_logic.py"]
