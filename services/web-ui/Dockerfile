FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY services/web-ui/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/web-ui/ ./
# The shared layer the preflight endpoint imports. Copying the whole service
# dir above also picks up the blueprint files (library_api, share_api,
# notes_api) that used to arrive only via the volume mount — the image now
# runs standalone too.
COPY services/common/ ./services/common/

EXPOSE 5000

CMD ["python", "app.py"]
