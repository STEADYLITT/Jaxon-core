# Jackson OS: API + worker in one image (ADR-031). Railway builds it from this file.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright APP_ENV=production DATA_DIR=/data

WORKDIR /app
COPY services/api services/api
COPY infra/deploy infra/deploy
# libatomic1: needed by Playwright's own installer before it can add Chromium's libraries
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
 && pip install -e ./services/api \
 && python -m playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

EXPOSE 8000
CMD ["bash", "/app/infra/deploy/start.sh"]
