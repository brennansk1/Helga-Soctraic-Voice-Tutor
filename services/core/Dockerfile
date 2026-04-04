FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY services/core/requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt

COPY services/core /app/services/core
COPY services/common /app/services/common

ENV PYTHONPATH=/app

WORKDIR /app/services/core

EXPOSE 5003

CMD ["python", "fsm_logic.py"]
