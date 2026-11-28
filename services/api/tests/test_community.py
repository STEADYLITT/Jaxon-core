"""ADR-044: Discord/Telegram access follows real Whop memberships; announcements are
approved public posts. Discord's and Telegram's networks are faked."""

import json
import uuid

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select

from jackson_api import connections as C
from jackson_api.connectors import community as K
from jackson_api.models import Approval, WhopMember
from jackson_api.worker import Worker

BOT = "MTE" + "d" * 50
TG = "123456:" + "T" * 30
GUILD, ROLE, CHAN, CHAT = "900", "777", "555", "-1001234"


class FakeChat:
    def __init__(self):
        self.calls = []

    def roles(self):
        return [(c.method, c.url.path.rsplit("/", 3)[-3]) for c in self.calls
                if "/roles/" in c.url.path]

    def tg(self, method):
        return [json.loads(c.content) for c in self.calls if c.url.path.endswith(method)]

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        p = req.url.path
        if req.url.host == "discord.com":
            assert req.headers["authorization"] == f"Bot {BOT}"
            assert req.headers["user-agent"].startswith("DiscordBot (")
            if p == f"/api/v10/guilds/{GUILD}":
                return httpx.Response(200, json={"id": GUILD, "name": "Steady Trades",
                                                 "approximate_member_count": 42,
                                                 "roles": [{"id": ROLE, "name": "Student"}]})
            if "/roles/" in p:
                assert "X-Audit-Log-Reason".lower() in req.headers
                return httpx.Response(204)
            if p == f"/api/v10/channels/{CHAN}/messages":
                return httpx.Response(200, json={"id": "m1"})
        if req.url.host == "api.telegram.org":
            assert p.startswith(f"/bot{TG}/")
            m = p.rsplit("/", 1)[-1]
            res = {"getMe": {"username": "jackson_bot"},
                   "getChat": {"title": "Steady VIP", "type": "supergroup"},
                   "createChatInviteLink": {"invite_link": "https://t.me/+abc"},
                   "sendMessage": {"message_id": 9}}.get(m, True)
            return httpx.Response(200, json={"ok": True, "result": res})
        return httpx.Response(404, json={"message": "unknown"})


@pytest.fixture()
def live(monkeypatch):
    fake = FakeChat()
    for k, v in {"DISCORD_BOT_TOKEN": BOT, "DISCORD_GUILD_ID": GUILD,
                 "DISCORD_MEMBER_ROLE_ID": ROLE, "DISCORD_ANNOUNCE_CHANNEL_ID": CHAN,
                 "TELEGRAM_BOT_TOKEN": TG, "TELEGRAM_CHAT_ID": CHAT}.items():
        monkeypatch.setenv(k, v)
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(K, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    return fake


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def members(owner_db, world):
    rows = [("mem_on", "active", "111", None), ("mem_off", "expired", "222", "333"),
            ("mem_nolink", "active", None, None)]
    for mid, st, d, t in rows:
        owner_db.add(WhopMember(tenant_id=world.tenant_a, membership_id=mid, status=st,
                                discord_id=d, telegram_id=t))
    owner_db.commit()


def sync(client, world, alice, maker, owner_db):
    r = client.post(f"/tenants/{world.tenant_a}/community/sync", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    return tasks[0].output


def test_connection_tests(client, world, alice, live):
    for cid, key in (("discord", "member_role"), ("telegram", "chat")):
        r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/{cid}/test", headers=alice)
        assert r.json()["ok"] and key in r.json()["result"], r.text
        assert BOT not in r.text and TG not in r.text
    assert r.json()["result"]["chat"] == "Steady VIP"


def test_roles_follow_whop_and_resync_is_a_no_op(client, world, alice, maker, owner_db,
                                                  drain, live):
    members(owner_db, world)
    out = sync(client, world, alice, maker, owner_db)
    assert out["discord_added"] == 1 and out["discord_removed"] == 1
    assert out["telegram_removed"] == 1
    assert sorted(live.roles()) == [("DELETE", "222"), ("PUT", "111")]
    assert live.tg("banChatMember")[0]["user_id"] == 333
    assert live.tg("unbanChatMember")[0]["only_if_banned"] is True
    before = len(live.calls)
    again = sync(client, world, alice, maker, owner_db)
    assert len(live.calls) == before and again["discord_added"] == 0  # nothing changed
    owner_db.expire_all()
    m = owner_db.scalar(select(WhopMember).where(WhopMember.membership_id == "mem_on"))
    assert m.discord_synced == "granted"


def test_telegram_invite_only_for_paying_members(client, world, alice, owner_db, live):
    members(owner_db, world)
    url = f"/tenants/{world.tenant_a}/community/telegram/invite"
    r = client.post(url, headers=alice, json={"membership_id": "mem_on"})
    assert r.status_code == 201 and r.json()["invite_link"] == "https://t.me/+abc"
    assert live.tg("createChatInviteLink")[0]["member_limit"] == 1
    assert client.post(url, headers=alice, json={"membership_id": "mem_off"}).status_code == 409


def test_announcement_needs_approval_and_passes_claims(client, world, alice, maker,
                                                       owner_db, drain, live):
    url = f"/tenants/{world.tenant_a}/community/announce"
    r = client.post(url, headers=alice, json={"platform": "discord", "text": "Live class at 7pm",
                                              "idempotency_key": uuid.uuid4().hex})
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    assert not [c for c in live.calls if c.url.path.endswith("/messages")]
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(r.json()["run_id"])))
    assert ap.category == "public_post"
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                json={"decision": "approve"})
    w.run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    assert json.loads([c for c in live.calls if c.url.path.endswith("/messages")][0].content) \
        == {"content": "Live class at 7pm"}
    bad = client.post(url, headers=alice, json={"platform": "telegram",
                                                "text": "Guaranteed profit every week",
                                                "idempotency_key": uuid.uuid4().hex})
    w.run_once()
    run, tasks = run_state(owner_db, bad.json()["run_id"])
    assert tasks[0].status == "failed" and "claims check" in tasks[0].error


def test_not_connected(client, world, alice, monkeypatch):
    for v in ("DISCORD_BOT_TOKEN", "TELEGRAM_BOT_TOKEN"):
        monkeypatch.delenv(v, raising=False)
    r = client.post(f"/tenants/{world.tenant_a}/community/sync", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "DISCORD_BOT_TOKEN" in r.text
