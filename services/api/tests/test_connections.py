"""Connections panel (ADR-038): LIVE/MOCK/ERROR, missing variable names, and a real
harmless test call per provider. The network is faked; request shapes follow each
provider's docs. Key values must never appear in any response."""

import json

import httpx
import pytest
from conftest import auth
from sqlalchemy import select

from jackson_api import connections as C
from jackson_api.models import AuditEvent

VARS = ["ANTHROPIC_API_KEY", "SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET",
        "SHOPIFY_ADMIN_API_TOKEN", "UPLOADPOST_API_KEY", "UPLOADPOST_USER", "RESEND_API_KEY",
        "RESEND_FROM", "RESEND_WEBHOOK_SECRET", "FAL_KEY", "FAL_VIDEO_MODEL",
        "FAL_VIDEO_CENTS_PER_SECOND", "SITE_DOMAIN_VERIFY", "S3_BUCKET", "WHOP_API_KEY",
        "WHOP_WEBHOOK_SECRET", "ELEVENLABS_API_KEY", "APIFY_TOKEN", "DISCORD_BOT_TOKEN",
        "DISCORD_GUILD_ID", "DISCORD_MEMBER_ROLE_ID", "DISCORD_ANNOUNCE_CHANNEL_ID",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "NETLIFY_AUTH_TOKEN", "BATCHDATA_API_TOKEN",
        "BATCHDATA_CENTS_PER_RECORD"]
SECRETS = {"ANTHROPIC_API_KEY": "sk-ant-" + "a" * 24, "SHOPIFY_CLIENT_SECRET": "shpss_" + "b" * 24,
           "UPLOADPOST_API_KEY": "up-" + "c" * 24, "RESEND_API_KEY": "re_" + "d" * 24,
           "FAL_KEY": "fal-" + "e" * 24}


class Providers:
    """Fake provider APIs, answering like their docs say."""

    def __init__(self):
        self.calls = []
        self.fail = set()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        host, path = req.url.host, req.url.path
        if host in self.fail:
            return httpx.Response(401, json={"error": {"message": "invalid x-api-key "
                                                       + SECRETS["ANTHROPIC_API_KEY"]}})
        if host == "api.anthropic.com" and path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "claude-a"}, {"id": "claude-b"}]})
        if host == "cha6cs-6c.myshopify.com" and path == "/admin/oauth/access_token":
            return httpx.Response(200, json={"access_token": "shpat_x", "expires_in": 86399})
        if host == "cha6cs-6c.myshopify.com":
            return httpx.Response(200, json={"data": {"shop": {
                "name": "Steady Goods", "myshopifyDomain": "cha6cs-6c.myshopify.com",
                "plan": {"displayName": "Basic"}}}})
        if host == "api.upload-post.com" and path == "/api/uploadposts/me":
            return httpx.Response(200, json={"success": True, "message": "Token is valid",
                                             "email": "steady@example.com", "plan": "Basic"})
        if host == "api.resend.com" and path == "/emails":
            return httpx.Response(200, json={"id": "em-test-1"})
        if host == "api.fal.ai" and path == "/v1/account/billing":
            return httpx.Response(200, json={"username": "steady", "credits": {
                "current_balance": 24.5, "currency": "USD"}})
        return httpx.Response(404, json={"error": "unexpected call"})


@pytest.fixture()
def net(monkeypatch):
    for v in VARS:
        monkeypatch.delenv(v, raising=False)
    fake = Providers()
    monkeypatch.setattr(C, "_new_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


def configure_all(monkeypatch):
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "cha6cs-6c.myshopify.com")
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "client-id")
    monkeypatch.setenv("UPLOADPOST_USER", "steady")
    monkeypatch.setenv("RESEND_FROM", "Steady <steady@steadytradesuniversity.com>")


def rows(client, world, h):
    r = client.get(f"/tenants/{world.tenant_a}/admin/connectors", headers=h)
    assert r.status_code == 200, r.text
    return {c["id"]: c for c in r.json()}


def test_everything_is_mock_with_the_missing_variable_names(client, world, make_token, net):
    s = rows(client, world, auth(make_token, world.alice))
    assert set(s) >= {"models", "shopify", "social", "email", "video", "whop", "dns", "sms",
                      "artifacts"}
    assert {v["mode"] for v in s.values()} == {"MOCK"}  # every connector, built or not
    assert s["shopify"]["missing"] == ["SHOPIFY_STORE_DOMAIN", "SHOPIFY_CLIENT_ID",
                                       "SHOPIFY_CLIENT_SECRET"]
    assert s["email"]["missing"] == ["RESEND_API_KEY", "RESEND_FROM"]
    assert s["hosting"]["built"] is True and s["hosting"]["missing"] == ["NETLIFY_AUTH_TOKEN"]
    r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/video/test",
                    headers=auth(make_token, world.alice)).json()
    assert r["ok"] is False and "FAL_KEY" in r["error"]
    assert net.calls == []  # nothing is called until the key exists


def test_each_test_makes_one_harmless_real_call(client, world, make_token, net, monkeypatch,
                                                owner_db):
    configure_all(monkeypatch)
    h = auth(make_token, world.alice)
    assert {k: v["mode"] for k, v in rows(client, world, h).items()
            if k in ("models", "shopify", "social", "email", "video")} == dict.fromkeys(
        ("models", "shopify", "social", "email", "video"), "LIVE")
    out = {}
    for cid in ("models", "shopify", "social", "email", "video"):
        r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/{cid}/test", headers=h)
        assert r.status_code == 200 and r.json()["ok"], (cid, r.text)
        out[cid] = r.json()["result"]
    assert out["models"]["models_available"] == 2
    assert out["shopify"] == {"store": "cha6cs-6c.myshopify.com", "name": "Steady Goods",
                              "plan": "Basic",
                              "access": "drafts only until a Launch approval covers publishing"}
    assert out["social"]["plan"] == "Basic" and out["social"]["posting_profile"] == "steady"
    assert out["video"] == {"account": "steady", "credit_balance": 24.5, "currency": "USD"}
    assert out["email"]["resend_email_id"] == "em-test-1"
    sent = json.loads([c for c in net.calls if c.url.host == "api.resend.com"][0].content)
    assert sent["to"] == [out["email"]["sent_to"]]  # only to the owner's own address
    assert sent["from"] == "Steady <steady@steadytradesuniversity.com>"
    gql = [c for c in net.calls if c.url.path.endswith("graphql.json")][0]
    assert b"mutation" not in gql.content  # read-only query
    # documented auth headers
    by = {c.url.host: c for c in net.calls}
    assert by["api.anthropic.com"].headers["x-api-key"] == SECRETS["ANTHROPIC_API_KEY"]
    assert by["api.upload-post.com"].headers["Authorization"].startswith("Apikey ")
    assert by["api.fal.ai"].headers["Authorization"] == f"Key {SECRETS['FAL_KEY']}"
    assert by["api.fal.ai"].url.params["expand"] == "credits"
    s = rows(client, world, h)
    assert s["video"]["last_test"]["ok"] is True
    assert owner_db.scalar(select(AuditEvent.id).where(
        AuditEvent.action == "connector.tested")) is not None


def test_a_bad_key_shows_error_and_never_the_key(client, world, make_token, net, monkeypatch):
    configure_all(monkeypatch)
    net.fail.add("api.anthropic.com")
    h = auth(make_token, world.alice)
    r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/models/test", headers=h)
    body = r.text
    assert r.json()["ok"] is False and r.json()["status"]["mode"] == "ERROR"
    assert "invalid x-api-key" in body
    listing = client.get(f"/tenants/{world.tenant_a}/admin/connectors", headers=h).text
    for v in SECRETS.values():
        assert v not in body and v not in listing


def test_fal_key_without_billing_permission_still_counts(client, world, make_token, net,
                                                         monkeypatch):
    configure_all(monkeypatch)
    orig = net.__call__

    def deny_billing(req):
        if req.url.host == "api.fal.ai":
            return httpx.Response(403, json={"error": {"type": "authorization_error",
                                                       "message": "Access denied"}})
        return orig(req)
    monkeypatch.setattr(C, "_new_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(deny_billing)))
    r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/video/test",
                    headers=auth(make_token, world.alice)).json()
    assert r["ok"] and r["result"]["key"] == "accepted"


def test_only_owner_or_admin(client, world, make_token, net):
    assert client.get(f"/tenants/{world.tenant_a}/admin/connectors",
                      headers=auth(make_token, world.carol)).status_code == 403  # viewer
    assert client.post(f"/tenants/{world.tenant_a}/admin/connectors/email/test",
                       headers=auth(make_token, world.carol)).status_code == 403
    assert client.get(f"/tenants/{world.tenant_b}/admin/connectors",
                      headers=auth(make_token, world.alice)).status_code in (403, 404)
    assert client.post(f"/tenants/{world.tenant_a}/admin/connectors/nope/test",
                       headers=auth(make_token, world.alice)).status_code == 404


def test_console_panel_tests_a_connection(client, world, make_token, net, monkeypatch):
    from test_console_ui import console

    monkeypatch.setenv("FAL_KEY", SECRETS["FAL_KEY"])
    with console(client, world, make_token(world.alice)) as (page, errors):
        row = page.wait_for_selector("#connections li[data-connector=video]")
        assert "LIVE" in row.text_content() and "not tested yet" in row.text_content()
        email = page.text_content("#connections li[data-connector=email]")
        assert "MOCK" in email and "needs RESEND_API_KEY + RESEND_FROM" in email
        assert page.is_disabled("#connections li[data-connector=email] button.cx-test")
        page.click("#connections li[data-connector=video] button.cx-test")
        page.wait_for_selector("#connections li[data-connector=video]:has-text('last test OK')")
        txt = page.text_content("#connections li[data-connector=video]")
        assert "credit balance 24.5" in txt and SECRETS["FAL_KEY"] not in page.content()
        assert errors == []
