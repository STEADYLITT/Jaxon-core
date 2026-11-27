"""Going online (ADR-031): deployment settings, first-boot setup, console sign-in."""

import base64
import os
import stat
import uuid
from contextlib import contextmanager

import pytest
from conftest import APP_PASSWORD, WORKER_PASSWORD, auth
from sqlalchemy import create_engine, make_url, text

from jackson_api.deploy_env import clerk_frontend_api, derive, load_or_create_secrets
from jackson_api.security import preflight

FAPI = "smoke-test.clerk.accounts.dev"
PK = "pk_test_" + base64.b64encode(f"{FAPI}$".encode()).decode().rstrip("=")
OWNER = "postgresql://postgres:s3cr%2Ft@postgres.railway.internal:5432/railway"


def test_clerk_frontend_api_comes_from_the_publishable_key():
    assert clerk_frontend_api(PK) == FAPI
    assert clerk_frontend_api(PK.replace("pk_test_", "pk_live_")) == FAPI
    for bad in (None, "", "nonsense", "pk_test_!!!", "pk_test_" + base64.b64encode(
            b"evil.com/x$").decode()):
        assert clerk_frontend_api(bad) is None


def test_derive_builds_a_safe_production_config_from_a_few_inputs(tmp_path):
    env = {"MIGRATION_DATABASE_URL": OWNER, "CLERK_PUBLISHABLE_KEY": PK,
           "RAILWAY_PUBLIC_DOMAIN": "jackson-production.up.railway.app",
           "DATA_DIR": "/data", "APP_ENV": "production"}  # the volume mount on the host
    s = load_or_create_secrets({"DATA_DIR": str(tmp_path)})
    out = derive(env, s)
    app, worker = make_url(out["DATABASE_URL"]), make_url(out["WORKER_DATABASE_URL"])
    assert (app.username, app.host, app.port, app.database) == (
        "jackson_app", "postgres.railway.internal", 5432, "railway")
    assert app.password == s["APP_DB_PASSWORD"] and worker.username == "jackson_worker"
    assert out["MIGRATION_DATABASE_URL"].startswith("postgresql+psycopg://")
    assert out["PUBLIC_BASE_URL"] == "https://jackson-production.up.railway.app"
    assert out["AUTH_ISSUER"] == f"https://{FAPI}"
    assert out["AUTH_JWKS_URL"] == f"https://{FAPI}/.well-known/jwks.json"
    assert out["AUTH_AUTHORIZED_PARTIES"] == out["PUBLIC_BASE_URL"]
    assert out["ARTIFACT_DIR"] == "/data/artifacts"
    # the result passes the production preflight as-is
    full = {**env, **out}
    assert all(c["ok"] for c in preflight(full)), [c for c in preflight(full) if not c["ok"]]


def test_explicit_settings_always_win(tmp_path):
    env = {"MIGRATION_DATABASE_URL": OWNER, "DATA_DIR": str(tmp_path),
           "DATABASE_URL": "postgresql+psycopg://jackson_app:mine@db/x",
           "PUBLIC_BASE_URL": "https://jackson.example.com",
           "RAILWAY_PUBLIC_DOMAIN": "other.up.railway.app"}
    out = derive(env, load_or_create_secrets(env))
    assert "DATABASE_URL" not in out and "PUBLIC_BASE_URL" not in out
    assert out["AUTH_AUTHORIZED_PARTIES"] == "https://jackson.example.com"


def test_secrets_are_generated_once_kept_private_and_stable(tmp_path):
    env = {"DATA_DIR": str(tmp_path)}
    first = load_or_create_secrets(env)
    assert all(len(v) >= 40 for v in first.values())
    f = tmp_path / ".jackson-secrets.json"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert load_or_create_secrets(env) == first  # same after a restart
    assert load_or_create_secrets({**env, "UNSUBSCRIBE_SECRET": "x" * 40})[
        "UNSUBSCRIBE_SECRET"] == "x" * 40


def test_export_prints_shell_safe_lines(tmp_path, monkeypatch, capsys):
    from jackson_api import deploy_env

    for k in ("DATABASE_URL", "WORKER_DATABASE_URL", "PUBLIC_BASE_URL", "AUTH_ISSUER",
              "AUTH_JWKS_URL", "AUTH_AUTHORIZED_PARTIES", "ARTIFACT_DIR", "UNSUBSCRIBE_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", OWNER)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert deploy_env.main(["x", "--export"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert lines and all(line.startswith("export ") for line in lines)
    db = [line for line in lines if line.startswith("export DATABASE_URL=")]
    assert db and "postgresql+psycopg://jackson_app:" in db[0]


# --- First boot against a real, empty database ----------------------------------------------------

@pytest.fixture()
def empty_db():
    admin_url = os.environ["JACKSON_TEST_ADMIN_URL"]
    name = f"jackson_boot_{uuid.uuid4().hex[:8]}"
    admin = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{name}"'))
    yield make_url(admin_url).set(database=name).render_as_string(hide_password=False)
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def test_first_boot_migrates_unlocks_roles_and_sets_up_the_owner(empty_db, tmp_path, capsys):
    from jackson_api.deploy_init import run

    # Role passwords are cluster-wide: reuse the test suite's so other tests keep working.
    env = {"MIGRATION_DATABASE_URL": empty_db, "DATA_DIR": str(tmp_path),
           "APP_DB_PASSWORD": APP_PASSWORD, "WORKER_DB_PASSWORD": WORKER_PASSWORD,
           "OWNER_EMAIL": "owner@example.com", "OWNER_AUTH_SUBJECT": "user_boot_test"}
    first = run(env)
    assert first["migrated"] and first["owner"]
    again = run(env)  # every boot runs this: nothing changes the second time
    assert again["owner"] == first["owner"]
    out = capsys.readouterr().out
    assert APP_PASSWORD not in out and WORKER_PASSWORD not in out

    derived = derive(env, load_or_create_secrets(env))
    eng = create_engine(derived["DATABASE_URL"])
    with eng.connect() as c:  # the API role can log in, and RLS still applies
        assert c.scalar(text("SELECT current_user")) == "jackson_app"
        assert c.scalar(text("SELECT count(*) FROM businesses")) == 0
        c.execute(text("SELECT set_config('app.tenant_id', :t, false)"), {"t": first["owner"]})
        assert c.scalar(text("SELECT count(*) FROM businesses")) == 5
    eng.dispose()


def test_first_boot_without_owner_says_what_to_do(empty_db, tmp_path, capsys):
    from jackson_api.deploy_init import run

    run({"MIGRATION_DATABASE_URL": empty_db, "DATA_DIR": str(tmp_path),
         "APP_DB_PASSWORD": APP_PASSWORD, "WORKER_DB_PASSWORD": WORKER_PASSWORD})
    assert "set OWNER_EMAIL and OWNER_AUTH_SUBJECT" in capsys.readouterr().out


# --- Console with Clerk sign-in -------------------------------------------------------------------

def test_console_config_and_csp_follow_the_clerk_key(client, monkeypatch):
    monkeypatch.delenv("CLERK_PUBLISHABLE_KEY", raising=False)
    assert client.get("/console/config.json").json()["clerk_publishable_key"] is None
    strict = client.get("/console").headers["content-security-policy"]
    assert "script-src 'self';" in strict and FAPI not in strict
    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", PK)
    assert client.get("/console/config.json").json()["clerk_publishable_key"] == PK
    csp = client.get("/console").headers["content-security-policy"]
    assert f"script-src 'self' https://{FAPI}" in csp
    assert f"connect-src 'self' https://{FAPI}" in csp
    assert "frame-ancestors 'none'" in csp


FAKE_CLERK = """
window.Clerk = {
  isSignedIn: true, user: { id: "%(sub)s" }, session: { getToken: async () => "%(token)s" },
  load: async (opts) => { window.__clerkLoadOpts = opts; },
  mountUserButton: () => {}, mountSignIn: () => {}, addListener: () => {},
};
"""


@contextmanager
def console_with_fake_clerk(client, monkeypatch, sub, token):
    """Clerk's real scripts are replaced by a stand-in that is already signed in, so this
    tests our console logic (token per call, picking the workspace), not Clerk."""
    from playwright.sync_api import sync_playwright

    monkeypatch.setenv("CLERK_PUBLISHABLE_KEY", PK)

    def route(r, request):
        url = request.url
        if url.startswith(f"https://{FAPI}/"):
            body = FAKE_CLERK % {"sub": sub, "token": token} if "clerk.browser" in url else ""
            return r.fulfill(status=200, body=body, content_type="text/javascript")
        path = url.removeprefix("http://console.test")
        resp = client.request(request.method, path, content=request.post_data_buffer,
                              headers={k: v for k, v in request.headers.items()
                                       if k.lower() in ("authorization", "content-type")})
        headers = {"content-type": resp.headers.get("content-type", "text/plain")}
        if "content-security-policy" in resp.headers:
            headers["content-security-policy"] = resp.headers["content-security-policy"]
        r.fulfill(status=resp.status_code, body=resp.content, headers=headers)

    exe = os.environ.get("BROWSER_EXECUTABLE") or (
        "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None)
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=exe, args=["--no-sandbox"] if exe else [])
        page = b.new_page()
        page.route("**/*", route)
        page.goto("http://console.test/console")
        try:
            yield page
        finally:
            b.close()


def test_console_signs_in_with_clerk_and_finds_the_workspace(client, world, make_token,
                                                             monkeypatch):
    with console_with_fake_clerk(client, monkeypatch, world.alice,
                                 make_token(world.alice)) as page:
        page.wait_for_selector("#dash:not([hidden])", timeout=15000)
        assert page.text_content("#banner") == "Running normally."
        assert page.is_hidden("#manual")
        opts = page.evaluate("({...window.__clerkLoadOpts, ui: undefined})")
        assert opts["signInForceRedirectUrl"] == "/console"
        assert opts["signUpForceRedirectUrl"] == "/console"
        assert opts["afterSignOutUrl"] == "/console"


def test_console_tells_a_new_owner_which_id_to_register(client, world, make_token,
                                                       monkeypatch):
    sub = f"user_new_{uuid.uuid4().hex[:6]}"
    with console_with_fake_clerk(client, monkeypatch, sub, make_token(sub)) as page:
        page.wait_for_selector("#err:has-text('OWNER_AUTH_SUBJECT')", timeout=15000)
        assert sub in page.text_content("#err")


def test_signed_in_user_without_access_gets_nothing(client, world, make_token):
    stranger = auth(make_token, "user_stranger")
    assert client.get("/me", headers=stranger).status_code == 403


def test_site_root_opens_the_console_and_keeps_the_clerk_handshake(client):
    r = client.get("/?__clerk_handshake=abc.def-ghi&x=1", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/console?__clerk_handshake=abc.def-ghi&x=1"
    plain = client.get("/", follow_redirects=False)
    assert plain.headers["location"] == "/console"
    assert client.get("/?__clerk_handshake=t").status_code == 200  # lands on the console


def test_first_boot_enables_shopify_drafts_once_when_configured(empty_db, tmp_path, capsys,
                                                                monkeypatch):
    from jackson_api.deploy_init import run

    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "cha6cs-6c.myshopify.com")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "client-id-1")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "s" * 32)
    env = {"MIGRATION_DATABASE_URL": empty_db, "DATA_DIR": str(tmp_path),
           "APP_DB_PASSWORD": APP_PASSWORD, "WORKER_DB_PASSWORD": WORKER_PASSWORD,
           "OWNER_EMAIL": "owner@example.com", "OWNER_AUTH_SUBJECT": "user_shop_test"}
    assert run(env)["grants"] == ["shopify.drafts"]
    assert run(env)["grants"] == []  # once only
    eng = create_engine(empty_db)
    with eng.connect() as c:
        rows = c.execute(text("SELECT tool_name, config::text FROM tool_grants")).all()
        assert rows == [("shopify.drafts", "{}")]  # no secret in the grant
        assert c.scalar(text("SELECT count(*) FROM audit_events WHERE action='tool.granted'")
                        ) == 1
    eng.dispose()
    out = capsys.readouterr().out
    assert "s" * 32 not in out and "shopify.drafts enabled" in out
