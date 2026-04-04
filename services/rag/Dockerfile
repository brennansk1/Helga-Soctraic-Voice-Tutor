FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/rag/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

COPY services/rag /app/services/rag
COPY services/common /app/services/common

ENV PYTHONPATH=/app

WORKDIR /app/services/rag

EXPOSE 5002

CMD ["python", "librarian.py"]
