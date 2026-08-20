FROM python:3.11-slim

WORKDIR /app

# espeak-ng is REQUIRED, not optional: kokoro's G2P (misaki) falls back to it
# for any word outside its dictionary, and for non-English languages entirely.
# Its absence does not fail at import — it fails later, on whichever unusual
# word a lesson happens to contain, which is the worst time to discover it.
# The upstream install instructions list it alongside the pip package.
RUN apt-get update && apt-get install -y \
    curl \
    libsndfile1 \
    espeak-ng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY tts_server.py .

EXPOSE 5005

CMD ["python", "tts_server.py"]
