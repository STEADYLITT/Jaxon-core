"""FastAPI entrypoint.

Phases 03-04: hosted-provider auth, tenant isolation, Postgres + migrations.
No external service calls. The command API (POST /commands, GET /runs/{id}, ...)
lands in phase 05.
"""

from fastapi import FastAPI
from sqlalchemy import text

from . import __version__
from .contracts import AutonomyLevel, CommandType, EntityName, TaskState
from .db import get_engine
from .routes import router
from .settings import load_settings

app = FastAPI(title="Jackson OS API", version=__version__)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    # Honest health: we report only what this process can verify about itself.
    return {
        "status": "ok",
        "service": "jackson-api",
        "version": __version__,
        "phase": "04-auth-and-database",
        "integrations": "none (no external connectors are live)",
    }


@app.get("/contracts")
def contracts() -> dict:
    return {
        "task_states": [s.value for s in TaskState],
        "command_types": [c.value for c in CommandType],
        "autonomy_levels": {lvl.name: lvl.value for lvl in AutonomyLevel},
        "entities": [e.value for e in EntityName],
    }


@app.get("/ready")
def ready() -> dict:
    """Reports whether auth and the database are configured and reachable. No secrets."""
    s = load_settings()
    db_state = "not_configured"
    if s.database_url:
        try:
            with get_engine(s.database_url).connect() as conn:
                conn.execute(text("SELECT 1"))
            db_state = "ok"
        except Exception:
            db_state = "unreachable"
    return {
        "auth": "configured" if s.auth_configured else "not_configured",
        "database": db_state,
    }
