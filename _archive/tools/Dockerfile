FROM python:3.9-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir kuzu requests

# Copy tool script
COPY night_audit.py .

CMD ["python", "night_audit.py"]
