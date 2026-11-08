"""API tests that need no database (DB-backed endpoints are in tests/integration)."""

import pytest
from fastapi.testclient import TestClient

from jaxon.api.app import create_app
from jaxon.runtime import Runtime
from tests.fixtures import ScriptedProvider


@pytest.fixture
def client(settings):
    rt = Runtime.build(settings, provider=ScriptedProvider([]))
    with TestClient(create_app(runtime=rt)) as c:
        yield c


def test_liveness(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert "x-request-id" in r.headers


def test_skills_list_and_get(client):
    names = [s["name"] for s in client.get("/api/v1/skills").json()]
    assert "math.calculate" in names
    assert client.get("/api/v1/skills/core.echo").json()["name"] == "core.echo"


def test_invoke_skill(client):
    r = client.post(
        "/api/v1/skills/math.calculate/invoke", json={"arguments": {"expression": "6*7"}}
    )
    assert r.status_code == 200 and r.json()["output"]["result"] == 42


def test_error_shapes(client):
    r = client.get("/api/v1/skills/nope")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.post("/api/v1/runs", json={"agent": "general"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation_failed"
    assert client.get("/does-not-exist").json()["error"]["code"] == "http_error"


def test_api_token_enforced(settings):
    from pydantic import SecretStr

    settings.jaxon_api_token = SecretStr("t0ken")
    rt = Runtime.build(settings, provider=ScriptedProvider([]))
    with TestClient(create_app(runtime=rt)) as c:
        assert c.get("/health").status_code == 200  # health stays open
        assert c.get("/api/v1/skills").status_code == 401
        assert c.get("/api/v1/skills", headers={"Authorization": "Bearer wrong"}).status_code == 401
        assert c.get("/api/v1/skills", headers={"Authorization": "Bearer t0ken"}).status_code == 200
