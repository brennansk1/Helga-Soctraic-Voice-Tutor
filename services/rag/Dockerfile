FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/rag/requirements.txt .
# No PyTorch here. It used to be installed explicitly with the CPU index URL,
# which contradicted this service's own requirements.txt — that file removed
# sentence-transformers precisely to drop the torch chain, and nothing in
# services/rag or services/common imports torch. The line was pulling a
# multi-hundred-MB wheel into the image, and its build time, for nothing.
#
# Under the Apple-native-first decision (docs/SPRINT_PLAN.md) it would be the
# wrong dependency even if something did need it: a container on macOS has no
# Metal, so torch there runs on virtualised CPU. Anything needing the GPU runs
# on the host — Ollama, STT and TTS all do.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY services/rag /app/services/rag
COPY services/common /app/services/common
# THE DOMAIN LAYER SHIPS TOO.
#
# `services/domains` was in neither this COPY list nor any compose mount, so
# in the container every `from services.domains...` raised ImportError — and
# every call site swallows that at DEBUG level (`course_builder.py`,
# `fsm_logic._domain_teaching`, `prompts.py`, `book_skeleton.py`). All four
# domain modules were dead in the deployed system while the whole test suite
# passed on the host, because on the host the package is simply there.
#
# Same signature defect as the twelve before it, one level up: not code that
# is never called, but code that is never PACKAGED.
COPY services/domains /app/services/domains

ENV PYTHONPATH=/app

WORKDIR /app/services/rag

EXPOSE 5002

CMD ["python", "librarian.py"]
