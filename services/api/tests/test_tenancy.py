"""Phase 03-04 acceptance: cross-tenant access denied (spec vol. 06), against real Postgres."""

import uuid

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from conftest import auth
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DBAPIError

from jackson_api.db import bind_tenant
from jackson_api.main import app
from jackson_api.models import AuditEvent, Base, Business
from jackson_api.seed import OWNER_BUSINESSES, seed_owner

# --- Authentication -----------------------------------------------------------

def test_no_token_401(client):
    assert client.get("/me").status_code == 401


def test_garbage_token_401(client):
    assert client.get("/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_valid_token_unknown_user_403(client, make_token):
    assert client.get("/me", headers=auth(make_token, "user_nobody")).status_code == 403


def test_auth_not_configured_fails_closed(database, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database["app_url"])
    for var in ("AUTH_ISSUER", "AUTH_JWKS_URL"):
        monkeypatch.delenv(var, raising=False)
    from jackson_api import deps

    deps._default_verifier.cache_clear()
    r = TestClient(app).get("/me", headers={"Authorization": "Bearer x"})
    assert r.status_code == 503


def test_me_lists_only_own_memberships(client, make_token, world):
    body = client.get("/me", headers=auth(make_token, world.alice)).json()
    assert {m["tenant_id"] for m in body["memberships"]} == {str(world.tenant_a)}
    assert body["memberships"][0]["role"] == "owner"


# --- Tenant isolation through the API -----------------------------------------

def test_member_sees_only_own_tenant_businesses(client, make_token, world):
    r = client.get(f"/tenants/{world.tenant_a}/businesses", headers=auth(make_token, world.alice))
    assert r.status_code == 200
    assert [b["id"] for b in r.json()] == [str(world.biz_a)]


def test_cross_tenant_list_denied(client, make_token, world):
    r = client.get(f"/tenants/{world.tenant_b}/businesses", headers=auth(make_token, world.alice))
    assert r.status_code == 403


def test_cross_tenant_object_id_not_found(client, make_token, world):
    """Alice uses her own tenant in the URL but B's business id."""
    r = client.get(f"/tenants/{world.tenant_a}/businesses/{world.biz_b}",
                   headers=auth(make_token, world.alice))
    assert r.status_code == 404


def test_cross_tenant_create_denied(client, make_token, world):
    r = client.post(f"/tenants/{world.tenant_b}/businesses",
                    json={"name": "sneaky", "kind": "x"}, headers=auth(make_token, world.alice))
    assert r.status_code == 403


def test_viewer_cannot_write(client, make_token, world):
    r = client.post(f"/tenants/{world.tenant_a}/businesses",
                    json={"name": "nope", "kind": "x"}, headers=auth(make_token, world.carol))
    assert r.status_code == 403


def test_owner_create_writes_audit_event(client, make_token, world):
    h = auth(make_token, world.alice)
    r = client.post(f"/tenants/{world.tenant_a}/businesses",
                    json={"name": "New Store", "kind": "ecommerce"}, headers=h)
    assert r.status_code == 201, r.text
    new_id = r.json()["id"]
    assert r.json()["tenant_id"] == str(world.tenant_a)

    events = client.get(f"/tenants/{world.tenant_a}/audit-events", headers=h).json()
    assert any(e["entity_id"] == new_id and e["action"] == "business.created" for e in events)
    # Bob cannot read A's audit trail.
    r = client.get(f"/tenants/{world.tenant_a}/audit-events", headers=auth(make_token, world.bob))
    assert r.status_code == 403


# --- Tenant isolation enforced by Postgres itself (defense in depth) -----------

def test_rls_hides_everything_without_tenant_scope(app_session, world):
    assert app_session.scalar(select(func.count()).select_from(Business)) == 0


def test_rls_shows_only_bound_tenant_even_without_where_clause(app_session, world):
    bind_tenant(app_session, world.tenant_a)
    tenants = set(app_session.scalars(select(Business.tenant_id)))
    assert tenants == {world.tenant_a}


def test_rls_blocks_insert_into_other_tenant(app_session, world):
    bind_tenant(app_session, world.tenant_a)
    app_session.add(Business(tenant_id=world.tenant_b, name="smuggled", kind="x"))
    with pytest.raises(DBAPIError, match="row-level security"):
        app_session.flush()


def test_tenant_scope_does_not_leak_past_transaction(app_session, world):
    bind_tenant(app_session, world.tenant_a)
    app_session.commit()
    assert app_session.scalar(select(func.count()).select_from(Business)) == 0


@pytest.mark.parametrize(
    "stmt", ["UPDATE audit_events SET action = 'x'", "DELETE FROM audit_events"]
)
def test_audit_log_is_append_only_for_app(app_session, world, stmt):
    bind_tenant(app_session, world.tenant_a)
    with pytest.raises(DBAPIError, match="permission denied"):
        app_session.execute(text(stmt))


def test_app_role_is_not_superuser(app_session):
    row = app_session.execute(
        text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    ).one()
    assert row == (False, False)


# --- Migrations and seed -------------------------------------------------------

def test_migrations_match_models(database):
    eng = create_engine(database["owner_url"])
    with eng.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    eng.dispose()
    assert diffs == []


def test_seed_is_idempotent(app_session):
    sub = f"user_owner_{uuid.uuid4().hex[:6]}"
    t1 = seed_owner(app_session, "owner@example.test", sub)
    t2 = seed_owner(app_session, "owner@example.test", sub)
    assert t1 == t2
    bind_tenant(app_session, t1)
    names = set(app_session.scalars(select(Business.name).where(Business.tenant_id == t1)))
    assert {n for _, n, _, _ in OWNER_BUSINESSES} <= names
    seeded = app_session.scalar(
        select(func.count()).select_from(AuditEvent)
        .where(AuditEvent.tenant_id == t1, AuditEvent.action == "business.seeded")
    )
    assert seeded == len(OWNER_BUSINESSES)
