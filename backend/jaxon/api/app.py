"""FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from jaxon import __version__
from jaxon.api.deps import require_token
from jaxon.api.routes import agents, health, memory, runs, skills, system
from jaxon.core.config import Settings, get_settings
from jaxon.core.errors import JaxonError
from jaxon.core.logging import bind_context, clear_context, configure_logging, get_logger
from jaxon.runtime import Runtime

log = get_logger(__name__)


def create_app(settings: Settings | None = None, runtime: Runtime | None = None) -> FastAPI:
    settings = settings or (runtime.settings if runtime else get_settings())
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        owned = runtime is None
        app.state.runtime = runtime or Runtime.build(settings)
        log.info(
            "app.started",
            environment=settings.environment,
            skills=len(app.state.runtime.skills),
            provider=app.state.runtime.provider.name,
        )
        try:
            yield
        finally:
            if owned:
                await app.state.runtime.aclose()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    if runtime is not None:
        app.state.runtime = runtime  # injected runtime is usable even without lifespan
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        clear_context()
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        bind_context(request_id=rid)
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        log.info(
            "http.request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=round((time.perf_counter() - start) * 1000, 2),
        )
        return response

    @app.exception_handler(JaxonError)
    async def jaxon_error(request: Request, exc: JaxonError) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("request.error", code=exc.code, message=exc.message)
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_failed",
                    "message": "Request validation failed",
                    "details": {
                        "errors": jsonable_encoder(
                            [{k: v for k, v in e.items() if k != "ctx"} for e in exc.errors()]
                        )
                    },
                }
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": "http_error", "message": str(exc.detail), "details": {}}},
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("request.unhandled")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "Internal server error",
                    "details": {},
                }
            },
        )

    app.include_router(health.router)
    api = APIRouter(prefix="/api/v1", dependencies=[Depends(require_token)])
    for r in (system.router, skills.router, agents.router, runs.router, memory.router):
        api.include_router(r)
    app.include_router(api)
    return app
