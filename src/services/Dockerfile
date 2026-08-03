# Minimal image for the Cloud Run crawl pipeline. Deliberately independent
# of the repo-root pyproject.toml (which also pulls in selenium/crawl4ai/
# llama-index for the local prototype) — this service only needs the deps
# listed in requirements.txt.
FROM python:3.12-slim

WORKDIR /app

COPY src/services/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY src/services ./src/services
ENV PYTHONPATH=/app/src
ENV PORT=8080

CMD exec uvicorn services.main:app --host 0.0.0.0 --port ${PORT}
