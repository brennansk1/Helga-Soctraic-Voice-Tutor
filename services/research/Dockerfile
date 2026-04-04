FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY services/research/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/research/research_server.py .

RUN useradd -m appuser
USER appuser

EXPOSE 5006

CMD ["python", "research_server.py"]
