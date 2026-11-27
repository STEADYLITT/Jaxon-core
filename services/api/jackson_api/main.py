"""FastAPI entrypoint.

Phases 03-05: hosted-provider auth, tenant isolation, Postgres + migrations,
typed commands and the run API. No external service calls. Nothing executes
runs yet: durable workers arrive in phase 06.
"""

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from . import __version__
from .contracts import AutonomyLevel, CommandType, EntityName, TaskState
from .db import get_engine
from .routes import router
from .routes_agents import router as agents_router
from .routes_browser import router as browser_router
from .routes_city import router as city_router
from .routes_connections import router as connections_router
from .routes_content import router as content_router
from .routes_crm import router as crm_router
from .routes_dashboard import router as dashboard_router
from .routes_launches import router as launches_router
from .routes_lines import router as lines_router
from .routes_memory import router as memory_router
from .routes_opportunities import router as opportunities_router
from .routes_policy import router as policy_router
from .routes_runs import router as runs_router
from .routes_shop import router as shop_router
from .routes_sites import router as sites_router
from .routes_tools import router as tools_router
from .security import SecurityMiddleware, assert_production_ready, is_production
from .settings import load_settings

assert_production_ready()  # fail closed: an unsafe production config never starts
app = FastAPI(title="Jackson OS API", version=__version__,
              **({"docs_url": None, "redoc_url": None, "openapi_url": None}
                 if is_production() else {}))
app.add_middleware(SecurityMiddleware)
app.include_router(router)
app.include_router(runs_router)
app.include_router(policy_router)
app.include_router(memory_router)
app.include_router(tools_router)
app.include_router(browser_router)
app.include_router(agents_router)
app.include_router(opportunities_router)
app.include_router(shop_router)
app.include_router(content_router)
app.include_router(crm_router)
app.include_router(dashboard_router)
app.include_router(launches_router)
app.include_router(lines_router)
app.include_router(sites_router)
app.include_router(city_router)
app.include_router(connections_router)


@app.get("/", include_in_schema=False)
def root(request: Request) -> RedirectResponse:
    """The site root opens the owner console. The query string is kept: Clerk returns
    here with ?__clerk_handshake=... and the console must receive it to finish sign-in."""
    q = request.url.query
    return RedirectResponse("/console" + (f"?{q}" if q else ""), status_code=307)


@app.get("/health")
def health() -> dict:
    # Honest health: we report only what this process can verify about itself.
    return {
        "status": "ok",
        "service": "jackson-api",
        "version": __version__,
        "phase": "21-launch-approvals",
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
