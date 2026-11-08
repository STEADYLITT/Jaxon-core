"""Structured logging (structlog) with request/run context propagation."""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog

_configured = False


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    global _configured
    log_level = logging.getLevelNamesMapping().get(level.upper(), logging.INFO)

    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
    # Route stdlib logging (uvicorn, sqlalchemy, httpx) through the same level.
    logging.basicConfig(level=log_level, stream=sys.stdout, format="%(message)s", force=True)
    _configured = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    if not _configured:
        configure_logging()
    return structlog.get_logger(name)


bind_context = structlog.contextvars.bind_contextvars
clear_context = structlog.contextvars.clear_contextvars
