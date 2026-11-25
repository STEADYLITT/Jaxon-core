"""FastAPI entrypoint.

Phase 02 scope: liveness and contract introspection only. No auth, no database,
no external calls. The spec API (POST /commands, GET /runs/{id}, ...) lands in
phase 05, after auth/tenant isolation (03) and database (04).
"""

from fastapi import FastAPI

from . import __version__
from .contracts import AutonomyLevel, CommandType, EntityName, TaskState

app = FastAPI(title="Jackson OS API", version=__version__)


@app.get("/health")
def health() -> dict:
    # Honest health: we report only what this process can verify about itself.
    return {
        "status": "ok",
        "service": "jackson-api",
        "version": __version__,
        "phase": "02-scaffold",
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
