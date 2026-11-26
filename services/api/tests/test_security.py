"""Phase 20: security hardening (ADR-030)."""

import os
import re
import subprocess
import sys
import uuid

import pytest
from fastapi.routing import APIRoute

from jackson_api import security
from jackson_api.main import app
from jackson_api.security import preflight

# Routes that are deliberately reachable without signing in, and why.
PUBLIC = {
    "/health": "liveness probe, no data",
    "/ready": "readiness probe (database reachable), no data",
    "/contracts": "public enum values for clients",
    "/console": "static page, no data; it calls the authenticated API",
    "/console/{name}": "static assets",
    "/console/config.json": "public Clerk publishable key (designed to be public)",
    "/u/{token}": "unsubscribe page; the HMAC-signed token is the credential",
}


def all_routes(router=None) -> list[APIRoute]:
    """Every APIRoute, including those inside included routers."""
    out = []
    for r in (router or app.router).routes:
        if isinstance(r, APIRoute):
            out.append(r)
        elif hasattr(r, "original_router"):
            out += all_routes(r.original_router)
    return out


def fill(path: str) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: "x" if m.group(1) in ("name", "token", "path")
                  else str(uuid.uuid4()), path)


def test_every_route_requires_sign_in_unless_listed_as_public(client):
    """Walks every API route. Anything new that forgets auth fails here."""
    checked = 0
    for route in all_routes():
        if route.path in PUBLIC:
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            r = client.request(method, fill(route.path), json={})
            assert r.status_code == 401, f"{method} {route.path} -> {r.status_code}"
            checked += 1
    assert checked > 60  # sanity: we really walked the API


def test_public_routes_are_exactly_the_reviewed_list():
    paths = {r.path for r in all_routes()}
    assert set(PUBLIC) <= paths
    assert len(paths) > 80


def test_security_headers(client, make_token, world):
    h = client.get("/health").headers
    assert h["x-content-type-options"] == "nosniff"
    assert h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    assert "strict-transport-security" not in h  # only in production (https)
    tok = {"Authorization": f"Bearer {make_token(world.alice)}"}
    private = client.get(f"/tenants/{world.tenant_a}/contacts", headers=tok).headers
    assert private["cache-control"] == "no-store"
    console = client.get("/console").headers  # route's own CSP is kept, not overwritten
    assert "script-src 'self'" in console["content-security-policy"]


def test_oversized_requests_are_rejected(client, monkeypatch):
    monkeypatch.setattr(security, "MAX_BODY_BYTES", 1000)
    r = client.post("/tenants/x/commands", content=b"x" * 5000,
                    headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_public_pages_are_rate_limited(client, monkeypatch):
    monkeypatch.setattr(security, "public_limiter", security.RateLimiter(3, 60))
    monkeypatch.setenv("UNSUBSCRIBE_SECRET", "s" * 40)
    codes = [client.get("/u/not-a-token").status_code for _ in range(5)]
    assert codes == [404, 404, 404, 429, 429]


GOOD_ENV = {
    "APP_ENV": "production",
    "DATABASE_URL": "postgresql+psycopg://jackson_app:pw@db.internal:5432/jackson",
    "WORKER_DATABASE_URL": "postgresql+psycopg://jackson_worker:pw@db.internal:5432/jackson",
    "AUTH_ISSUER": "https://clerk.example.com", "AUTH_JWKS_URL":
    "https://clerk.example.com/.well-known/jwks.json",
    "AUTH_AUTHORIZED_PARTIES": "https://console.example.com",
    "UNSUBSCRIBE_SECRET": "u" * 40, "PUBLIC_BASE_URL": "https://api.example.com",
    "ARTIFACT_DIR": "/var/lib/jackson/artifacts",
}


def test_preflight_passes_a_safe_production_config():
    assert all(c["ok"] for c in preflight(GOOD_ENV)), preflight(GOOD_ENV)


@pytest.mark.parametrize("change,check", [
    ({"DATABASE_URL": "postgresql://postgres:pw@db/jackson"}, "least_privilege_db_role"),
    ({"WORKER_DATABASE_URL": GOOD_ENV["DATABASE_URL"]}, "worker_db_role"),
    ({"AUTH_ISSUER": "http://clerk.example.com"}, "auth_configured"),
    ({"UNSUBSCRIBE_SECRET": "short"}, "unsubscribe_secret"),
    ({"PUBLIC_BASE_URL": "http://api.example.com"}, "public_base_url"),
    ({"ARTIFACT_DIR": "./var/artifacts"}, "durable_artifacts"),
    ({"ARTIFACT_DIR": "/tmp/a"}, "durable_artifacts"),
    ({"CODE_SANDBOX": "process"}, "no_unsandboxed_code"),
])
def test_preflight_catches_unsafe_settings(change, check):
    failed = {c["check"] for c in preflight({**GOOD_ENV, **change}) if not c["ok"]}
    assert failed == {check}


def _start(env: dict) -> subprocess.CompletedProcess:
    code = ("from fastapi.testclient import TestClient; from jackson_api.main import app; "
            "c = TestClient(app); r = c.get('/health'); "
            "print(c.get('/docs').status_code, r.headers.get('strict-transport-security'))")
    clean = {k: v for k, v in os.environ.items() if not k.startswith(
        ("APP_ENV", "DATABASE_URL", "AUTH_", "UNSUBSCRIBE", "PUBLIC_BASE", "ARTIFACT", "S3_",
         "WORKER_DATABASE", "CODE_SANDBOX"))}
    return subprocess.run([sys.executable, "-c", code], env={**clean, **env},
                          capture_output=True, text=True, timeout=60)


def test_production_refuses_to_start_with_unsafe_config():
    p = _start({"APP_ENV": "production"})
    assert p.returncode != 0 and "refusing to start in production" in p.stderr
    assert "unsubscribe_secret" in p.stderr


def test_production_starts_with_safe_config_and_hides_docs():
    p = _start(GOOD_ENV)
    assert p.returncode == 0, p.stderr
    status, hsts = p.stdout.strip().split(" ", 1)
    assert status == "404" and hsts.startswith("max-age=")
    assert GOOD_ENV["UNSUBSCRIBE_SECRET"] not in p.stdout + p.stderr


def test_preflight_cli_prints_names_never_values():
    p = subprocess.run([sys.executable, "-m", "jackson_api.preflight"],
                       env={**os.environ, **GOOD_ENV}, capture_output=True, text=True)
    assert p.returncode == 0 and "10/10 checks passed" in p.stdout
    assert "u" * 40 not in p.stdout and "pw@" not in p.stdout


def test_code_execution_is_off_in_production(monkeypatch):
    from jackson_api.tools.builtin import PythonIn, run_python
    from jackson_api.worker_errors import NonRetryableError

    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(NonRetryableError, match="disabled in production"):
        run_python(PythonIn(code="print(1)"), None)
