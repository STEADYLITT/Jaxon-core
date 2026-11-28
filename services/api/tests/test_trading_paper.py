"""ADR-047: APEX-7 on an Alpaca PAPER account. The live host is never called; futures map
to ETF proxies with a note; KPIs come from real paper snapshots. Alpaca is faked."""

import json
import uuid

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select

from jackson_api import connections as C
from jackson_api.connectors import broker as B
from jackson_api.lines import kpis
from jackson_api.models import Approval
from jackson_api.worker import Worker

KID, SEC = "PK" + "A" * 18, "s" * 40


class FakeAlpaca:
    def __init__(self):
        self.calls = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        assert req.url.host in ("paper-api.alpaca.markets", "data.alpaca.markets")
        assert req.headers["apca-api-key-id"] == KID and req.headers["apca-api-secret-key"] == SEC
        p = req.url.path
        if p == "/v2/account":
            return httpx.Response(200, json={"account_number": "PA3XYZ", "status": "ACTIVE",
                                             "equity": "101250.50", "last_equity": "100900.00",
                                             "cash": "60000", "buying_power": "200000"})
        if p == "/v2/positions":
            return httpx.Response(200, json=[{"symbol": "QQQ", "qty": "10",
                                              "market_value": "5100", "unrealized_pl": "42.5"}])
        if p == "/v2/account/portfolio/history":
            return httpx.Response(200, json={"equity": [100000, 100600, 101250.5],
                                             "profit_loss": [0, 600, 650.5],
                                             "base_value": 100000})
        if p.startswith("/v2/stocks/") and p.endswith("/bars"):
            assert req.url.params["feed"] == "iex"
            return httpx.Response(200, json={"bars": [{"t": "2026-11-27T05:00:00Z", "o": 510,
                                                       "h": 515, "l": 505, "c": 512, "v": 9}]})
        if p == "/v2/orders":
            body = json.loads(req.content)
            return httpx.Response(200, json={"id": "ord1", "status": "accepted", **body})
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture()
def live(monkeypatch):
    fake = FakeAlpaca()
    monkeypatch.setenv("ALPACA_PAPER_KEY_ID", KID)
    monkeypatch.setenv("ALPACA_PAPER_SECRET_KEY", SEC)
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(B, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    return fake


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def run(client, world, alice, maker, owner_db, path, body):
    r = client.post(f"/tenants/{world.tenant_a}/trading/paper/{path}", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex, **body})
    assert r.status_code == 202, r.text
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    rid = r.json()["run_id"]
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(rid),
                                                Approval.status == "pending"))
    if ap is not None:
        assert ap.category == "simulation"
        client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                    json={"decision": "approve"})
        w.run_once()
    runrow, tasks = run_state(owner_db, rid)
    assert runrow.status == "succeeded", tasks[0].error
    return r.json(), tasks[0].output


def test_paper_host_only_and_futures_proxies():
    assert B.PAPER == "https://paper-api.alpaca.markets"
    assert B.symbol("MNQZ6")[0] == "QQQ" and "QQQ as a proxy" in B.symbol("MNQ")[1]
    assert B.symbol("aapl") == ("AAPL", None)
    with pytest.raises(Exception, match="future"):
        B.symbol("CL")


def test_connection_test_reads_the_paper_account(client, world, alice, live):
    r = client.post(f"/tenants/{world.tenant_a}/admin/connectors/broker/test", headers=alice)
    assert r.json()["ok"] and r.json()["result"]["paper_account"] == "PA3XYZ"
    assert SEC not in r.text and all(c.url.host != "api.alpaca.markets" for c in live.calls)


def test_snapshot_feeds_the_trading_kpis(client, world, alice, maker, owner_db, drain, live):
    _, out = run(client, world, alice, maker, owner_db, "snapshot", {"symbols": ["MNQ"]})
    assert out["equity"] == 101250.5 and out["day_pl"] == 350.5 and out["month_pl"] == 1250.5
    assert out["symbols"] == ["QQQ"] and "proxy" in out["notes"][0]
    assert "/v2/stocks/QQQ/bars" in [c.url.path for c in live.calls]
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_a, "trading")}
    assert k["Paper P&L, last month (Alpaca paper)"]["value"] == 1250.5
    assert k["Live orders placed"]["value"] == 0
    kb = {x["name"]: x for x in kpis(owner_db, world.tenant_b, "trading")}
    assert kb["Paper equity"]["value"] is None  # nothing invented for a tenant with no data


def test_paper_order_goes_to_the_paper_host_with_the_proxy(client, world, alice, maker,
                                                           owner_db, drain, live):
    resp, out = run(client, world, alice, maker, owner_db, "orders",
                    {"symbol": "MNQ", "qty": 2, "side": "buy"})
    assert resp["symbol"] == "QQQ" and out["paper"] is True and out["status"] == "accepted"
    [order] = [c for c in live.calls if c.url.path == "/v2/orders"]
    assert order.url.host == "paper-api.alpaca.markets"
    assert json.loads(order.content)["symbol"] == "QQQ"


def test_live_trading_stays_disabled():
    from jackson_api.policy import NEVER, Category
    assert Category.LIVE_TRADE in NEVER  # paper orders are SIMULATION; live is never allowed
    import re
    src = open(B.__file__).read()
    assert not re.search(r"(?<!paper-)api\.alpaca\.markets", src)  # no live host anywhere


def test_not_connected(client, world, alice, monkeypatch):
    monkeypatch.delenv("ALPACA_PAPER_KEY_ID", raising=False)
    r = client.post(f"/tenants/{world.tenant_a}/trading/paper/snapshot", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "ALPACA_PAPER_KEY_ID" in r.text
