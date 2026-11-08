FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY jaxon ./jaxon
RUN pip install ".[browser]"

COPY alembic.ini ./
COPY migrations ./migrations

# Optional: install Chromium for browser.page_text (adds ~400MB). Enable with
#   docker compose build --build-arg INSTALL_BROWSER=1
ARG INSTALL_BROWSER=0
RUN if [ "$INSTALL_BROWSER" = "1" ]; then python -m playwright install --with-deps chromium; fi

RUN useradd --create-home --uid 1000 jaxon
USER jaxon

EXPOSE 8000
CMD ["uvicorn", "jaxon.main:app", "--host", "0.0.0.0", "--port", "8000"]
