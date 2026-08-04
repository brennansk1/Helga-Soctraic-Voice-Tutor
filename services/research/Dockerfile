FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY services/research/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the WHOLE service directory, not one file.
#
# This was `COPY services/research/research_server.py .` — a single file. When
# ranking.py was extracted into its own module for testability, nothing added
# it here, so the container crash-looped on
# `ModuleNotFoundError: No module named 'ranking'` from the moment the refactor
# landed. The research service was simply never running: grounding fell back to
# Wikipedia-only, source_confidence capped at 0.40 against a 0.5 floor, and
# every concept in every course shipped with a "Limited sources" marker.
#
# The container was diagnosed as "SearXNG is down" for weeks. SearXNG was fine.
COPY services/research/ ./

RUN useradd -m appuser
USER appuser

EXPOSE 5006

CMD ["python", "research_server.py"]
