# Minimal image for the Cloud Run crawl pipeline. Deliberately independent
# of the repo-root pyproject.toml (which also pulls in selenium/llama-index
# for the local prototype) — this service only needs the deps listed in
# requirements.txt. crawl4ai IS used here (unlike the other prototype-only
# deps) — see crawl4ai_client.py, the self-hosted default scraper backend.
FROM python:3.12-slim

WORKDIR /app

COPY src/services/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
# crawl4ai/Playwright need an actual browser binary + its OS-level libs
# (libnss3, libatk, etc.) - --with-deps apt-installs those on this Debian
# base image. This is what grows the image noticeably (~500MB+) compared to
# the old Firecrawl-only image, in exchange for no per-page SaaS cost.
RUN python -m playwright install --with-deps chromium

COPY src/services ./src/services
ENV PYTHONPATH=/app/src
ENV PORT=8080

CMD exec uvicorn services.main:app --host 0.0.0.0 --port ${PORT}
