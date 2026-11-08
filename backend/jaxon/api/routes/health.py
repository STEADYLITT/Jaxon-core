from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from jaxon import __version__
from jaxon.api.deps import get_runtime
from jaxon.runtime import Runtime

router = APIRouter(tags=["health"])


@router.get("/health")
async def liveness() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/health/ready")
async def readiness(rt: Runtime = Depends(get_runtime)) -> JSONResponse:
    checks: dict[str, dict[str, object]] = {}
    try:
        async with rt.db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            ext = await conn.scalar(
                text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
            rev = await conn.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
        checks["database"] = {"ok": True}
        checks["pgvector"] = {"ok": ext is not None, "version": ext}
        checks["migrations"] = {"ok": rev is not None, "revision": rev}
    except Exception as e:
        checks.setdefault("database", {"ok": False, "error": type(e).__name__})
        checks.setdefault("pgvector", {"ok": False})
        checks.setdefault("migrations", {"ok": False})
    try:
        await rt.redis.ping()
        checks["redis"] = {"ok": True}
    except Exception as e:
        checks["redis"] = {"ok": False, "error": type(e).__name__}
    # Credential presence only - not a live call.
    checks["model_provider"] = {
        "ok": rt.provider.is_configured(),
        "provider": rt.provider.name,
        "note": "credential presence only; no live call is made",
    }
    core = ("database", "pgvector", "migrations", "redis")
    ready = all(bool(checks[k]["ok"]) for k in core)
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"status": "ready" if ready else "not_ready", "checks": checks},
    )
