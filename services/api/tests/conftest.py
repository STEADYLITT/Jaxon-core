"""Test fixtures.

DB tests need a real PostgreSQL (row-level security can't be faked). Point
JACKSON_TEST_ADMIN_URL at a superuser connection to a scratch server, e.g.
    postgresql+psycopg://postgres@localhost:5432/postgres
Each test session creates and drops its own database `jackson_test_<random>`.

Auth tests use a locally generated RSA key and a TEST issuer. No real auth
provider is contacted.
"""

import os
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import jwt
import pytest
from alembic import command
from alembic.config import Config
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from jackson_api import deps
from jackson_api.auth import AuthConfig, TokenVerifier
from jackson_api.main import app
from jackson_api.models import Business, Membership, RunEvent, Task, Tenant, User, WorkflowRun
from jackson_api.worker import Worker

API_DIR = Path(__file__).resolve().parents[1]
TEST_ISSUER = "https://test-issuer.invalid"  # .invalid TLD: guaranteed never to resolve
TEST_PARTY = "http://localhost:3000"
APP_PASSWORD = "test-only-app-password"


@pytest.fixture(scope="session")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="session")
def verifier(rsa_key) -> TokenVerifier:
    public = rsa_key.public_key()
    return TokenVerifier(
        AuthConfig(
            issuer=TEST_ISSUER,
            jwks_url="https://test-issuer.invalid/.well-known/jwks.json",
            authorized_parties=frozenset({TEST_PARTY}),
        ),
        key_resolver=lambda _token: public,
    )


@pytest.fixture(scope="session")
def make_token(rsa_key):
    def _make(sub: str, *, key=None, issuer=TEST_ISSUER, azp=TEST_PARTY, exp_in=300, **extra):
        now = int(time.time())
        claims = {"sub": sub, "iss": issuer, "iat": now, "nbf": now, "exp": now + exp_in}
        if azp is not None:
            claims["azp"] = azp
        claims.update(extra)
        return jwt.encode(claims, key or rsa_key, algorithm="RS256")

    return _make


@pytest.fixture(scope="session")
def database() -> Iterator[dict]:
    admin_url = os.environ.get("JACKSON_TEST_ADMIN_URL")
    if not admin_url:
        pytest.fail(
            "JACKSON_TEST_ADMIN_URL is not set. DB tests need a real PostgreSQL; "
            "see services/api/tests/conftest.py"
        )
    name = f"jackson_test_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    owner_url = make_url(admin_url).set(database=name)

    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.attributes["url"] = owner_url.render_as_string(hide_password=False)
    command.upgrade(cfg, "head")

    with admin.connect() as c:
        c.execute(text(f"ALTER ROLE jackson_app LOGIN PASSWORD '{APP_PASSWORD}'"))
    app_url = owner_url.set(username="jackson_app", password=APP_PASSWORD)

    yield {
        "owner_url": owner_url.render_as_string(hide_password=False),
        "app_url": app_url.render_as_string(hide_password=False),
        "alembic_config": cfg,
    }

    from jackson_api.db import get_engine

    get_engine.cache_clear()
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture()
def client(database, verifier, monkeypatch) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", database["app_url"])
    app.dependency_overrides[deps.get_verifier] = lambda: verifier
    yield TestClient(app)
    app.dependency_overrides.clear()


@dataclass
class World:
    tenant_a: uuid.UUID
    tenant_b: uuid.UUID
    biz_a: uuid.UUID
    biz_b: uuid.UUID
    alice: str  # owner of A
    bob: str  # owner of B
    carol: str  # viewer of A


@pytest.fixture()
def world(database) -> World:
    """Insert fixtures as the schema owner (bypasses RLS on purpose, setup only)."""
    eng = create_engine(database["owner_url"])
    tag = uuid.uuid4().hex[:6]
    with Session(eng) as s:
        ta, tb = Tenant(name=f"A-{tag}"), Tenant(name=f"B-{tag}")
        users = {n: User(auth_subject=f"user_{n}_{tag}", email=f"{n}@example.test")
                 for n in ("alice", "bob", "carol")}
        s.add_all([ta, tb, *users.values()])
        s.flush()
        s.add_all([
            Membership(tenant_id=ta.id, user_id=users["alice"].id, role="owner"),
            Membership(tenant_id=tb.id, user_id=users["bob"].id, role="owner"),
            Membership(tenant_id=ta.id, user_id=users["carol"].id, role="viewer"),
        ])
        ba = Business(tenant_id=ta.id, name="A biz", kind="ecommerce")
        bb = Business(tenant_id=tb.id, name="B biz", kind="agency")
        s.add_all([ba, bb])
        s.commit()
        w = World(ta.id, tb.id, ba.id, bb.id, *(users[n].auth_subject for n in
                                                ("alice", "bob", "carol")))
    eng.dispose()
    return w


def auth(make_token, sub):
    return {"Authorization": f"Bearer {make_token(sub)}"}


@pytest.fixture()
def app_session(database):
    eng = create_engine(database["app_url"])
    with Session(eng) as s:
        yield s
    eng.dispose()


# --- Worker fixtures (phase 06+) -----------------------------------------------------

WORKER_PASSWORD = "test-only-worker-password"


@pytest.fixture(scope="session")
def worker_url(database):
    admin = create_engine(database["owner_url"], isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f"ALTER ROLE jackson_worker LOGIN PASSWORD '{WORKER_PASSWORD}'"))
    admin.dispose()
    from sqlalchemy.engine import make_url
    return make_url(database["app_url"]).set(
        username="jackson_worker", password=WORKER_PASSWORD).render_as_string(False)


@pytest.fixture()
def maker(worker_url):
    eng = create_engine(worker_url)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    eng.dispose()


@pytest.fixture()
def owner_db(database):
    eng = create_engine(database["owner_url"])
    with Session(eng) as s:
        yield s
    eng.dispose()


@pytest.fixture()
def drain(owner_db):
    """Park every other test's leftover queued work so each test sees only its own."""
    owner_db.execute(update(WorkflowRun).where(WorkflowRun.status.in_(["queued", "running"]))
                     .values(paused=True))
    owner_db.commit()


def new_run(client, world, make_token, objective="Find a product", tenant=None, who=None):
    who = who or world.alice
    r = client.post(f"/tenants/{tenant or world.tenant_a}/commands",
                    headers=auth(make_token, who),
                    json={"type": "PLAN", "objective": objective,
                          "idempotency_key": f"k-{uuid.uuid4().hex}"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def run_state(owner_db, run_id):
    owner_db.expire_all()
    run = owner_db.get(WorkflowRun, uuid.UUID(run_id))
    tasks = owner_db.scalars(select(Task).where(Task.run_id == run.id)
                             .order_by(Task.sequence)).all()
    return run, tasks


def events(owner_db, run_id):
    return [e.type for e in owner_db.scalars(
        select(RunEvent).where(RunEvent.run_id == uuid.UUID(run_id)).order_by(RunEvent.id))]


def fast_worker(maker, handlers, **kw):
    kw.setdefault("retry_base_seconds", 0)
    return Worker(maker, handlers=handlers, **kw)


@pytest.fixture(autouse=True)
def _short_console_streams(monkeypatch):
    """Browser tests pass every request through the synchronous TestClient, so the console's
    live-feed stream must return quickly instead of holding the request open."""
    monkeypatch.setenv("CONSOLE_EVENTS_TIMEOUT", "0.3")


@pytest.fixture(autouse=True)
def _no_live_model_calls(monkeypatch):
    """Tests must never reach a real model provider, whatever the developer's shell has."""
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "OPENAI_MODEL",
                "JACKSON_MODEL_PROVIDER", "LOCAL_MODEL_BASE_URL", "LOCAL_MODEL_NAME"):
        monkeypatch.delenv(var, raising=False)
