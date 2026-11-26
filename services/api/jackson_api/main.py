"""FastAPI entrypoint.

Phases 03-05: hosted-provider auth, tenant isolation, Postgres + migrations,
typed commands and the run API. No external service calls. Nothing executes
runs yet: durable workers arrive in phase 06.
"""

from fastapi import FastAPI
from sqlalchemy import text

from . import __version__
from .contracts import AutonomyLevel, CommandType, EntityName, TaskState
from .db import get_engine
from .routes import router
from .routes_agents import router as agents_router
from .routes_browser import router as browser_router
from .routes_memory import router as memory_router
from .routes_opportunities import router as opportunities_router
from .routes_policy import router as policy_router
from .routes_runs import router as runs_router
from .routes_tools import router as tools_router
from .settings import load_settings

app = FastAPI(title="Jackson OS API", version=__version__)
app.include_router(router)
app.include_router(runs_router)
app.include_router(policy_router)
app.include_router(memory_router)
app.include_router(tools_router)
app.include_router(browser_router)
app.include_router(agents_router)
app.include_router(opportunities_router)


@app.get("/health")
def health() -> dict:
    # Honest health: we report only what this process can verify about itself.
    return {
        "status": "ok",
        "service": "jackson-api",
        "version": __version__,
        "phase": "13-opportunity-research",
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
