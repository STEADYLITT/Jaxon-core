"""ADR-054: APEX-7 results (uploaded or pulled) and real CME futures bars from Databento.
Every stat is computed from real trades; data pulls show Databento's quote first."""

import json
import uuid

import httpx
import pytest
from conftest import auth, run_state
from sqlalchemy import select

from jackson_api import connections as C
from jackson_api.connectors import apex7 as X
from jackson_api.lines import kpis
from jackson_api.models import Approval
from jackson_api.worker import Worker

KEY = "db-" + "k" * 29

TRADES = [  # MNQ: $2 per point
    {"side": "long", "qty": 1, "entry_price": 21000, "exit_price": 21050},   # +100
    {"side": "short", "qty": 2, "entry_price": 21100, "exit_price": 21120},  # -80
    {"side": "long", "qty": 1, "pnl": 30.0},                                 # +30
    {"side": "long", "qty": 1},                                              # unknown: skipped
]
CSV = ("Trade #,Type,Date/Time,Price Entry,Price Exit,Contracts,Profit USD\n"
       "1,Entry Long,2026-11-01 09:30,21000,21010,1,20\n"
       "2,Entry Short,2026-11-02 09:30,21100,21090,1,-15\n")


def test_pnl_and_stats_from_real_trades():
    t = X.normalize(TRADES, "MNQ")
    assert [x["pnl"] for x in t] == [100.0, -80.0, 30.0]  # the 4th has no result: left out
    s = X.stats(t)
    assert s["net_pnl"] == 50.0 and s["win_rate_pct"] == 66.7
    assert s["profit_factor"] == 1.62 and s["max_drawdown"] == 80.0
    assert X.point_value("MNQZ6") == 2.0 and X.point_value("ES") == 50.0
    assert X.point_value("QQQ") == 1.0
    c = X.normalize(X.parse_csv(CSV), "MNQ")
    assert [x["pnl"] for x in c] == [20.0, -15.0] and c[1]["side"] == "short"


@pytest.fixture()
def alice(make_token, world):
    return auth(make_token, world.alice)


def test_upload_feeds_the_trading_kpis(client, world, alice, maker, owner_db, drain):
    r = client.post(f"/tenants/{world.tenant_a}/trading/backtests", headers=alice,
                    json={"name": "APEX-7 v3 Nov", "symbol": "MNQ", "trades": TRADES,
                          "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    saved = tasks[0].output["runs"][0]
    assert saved["skipped_trades"] == 1 and saved["net_pnl"] == 50.0
    k = {x["name"]: x for x in kpis(owner_db, world.tenant_a, "trading")}
    assert k["Backtests recorded (APEX-7)"]["value"] == 1
    assert k["Latest backtest net P&L"]["value"] == 50.0
    assert k["Latest backtest win rate"]["value"] == 66.7
    kb = {x["name"]: x for x in kpis(owner_db, world.tenant_b, "trading")}
    assert kb["Latest backtest net P&L"]["value"] is None  # nothing invented


def test_pull_from_apex7_feed(client, world, alice, maker, owner_db, drain, monkeypatch):
    monkeypatch.setenv("APEX7_RESULTS_URL", "https://apex7.example/results.json")
    monkeypatch.setenv("APEX7_RESULTS_TOKEN", "tok-" + "a" * 20)

    def handler(req):
        assert req.headers["authorization"] == "Bearer tok-" + "a" * 20
        return httpx.Response(200, json=[{"name": "live sim", "kind": "sim", "csv": CSV}])
    monkeypatch.setattr(X, "_new_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(C, "_new_client",
                        lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    t = client.post(f"/tenants/{world.tenant_a}/admin/connectors/apex7/test", headers=alice)
    assert t.json()["ok"] and t.json()["result"]["runs_published"] == 1
    r = client.post(f"/tenants/{world.tenant_a}/trading/backtests/pull", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    Worker(maker, retry_base_seconds=0).run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    assert tasks[0].output["runs"][0]["kind"] == "sim"
    assert tasks[0].output["runs"][0]["net_pnl"] == 5.0


class FakeDatabento:
    def __init__(self):
        self.calls = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        assert req.headers["authorization"].startswith("Basic ")
        p = req.url.path
        if p == "/v0/metadata.list_datasets":
            return httpx.Response(200, json=["GLBX.MDP3", "XNAS.ITCH"])
        form = dict(x.split("=", 1) for x in req.content.decode().split("&"))
        assert form["dataset"] == "GLBX.MDP3" and form["symbols"] == "MNQ.c.0"
        assert form["stype_in"] == "continuous"
        if p == "/v0/metadata.get_cost":
            return httpx.Response(200, json=0.0421)
        if p == "/v0/timeseries.get_range":
            lines = [json.dumps({"ts_event": "2026-11-03T00:00:00Z", "open": "21000.25",
                                 "high": "21100.0", "low": "20950.5", "close": "21080.0",
                                 "volume": 12345, "symbol": "MNQZ6"}),
                     json.dumps({"hd": {"ts_event": "2026-11-04T00:00:00Z"},
                                 "open": 21080000000000, "high": 21150000000000,
                                 "low": 21000000000000, "close": 21120000000000,
                                 "volume": 9999})]
            return httpx.Response(200, text="\n".join(lines))
        return httpx.Response(404, json={"detail": "nope"})


def test_futures_bars_cost_first_then_real_data(client, world, alice, maker, owner_db, drain,
                                                monkeypatch):
    fake = FakeDatabento()
    monkeypatch.setenv("DATABENTO_API_KEY", KEY)
    mk = lambda: httpx.Client(transport=httpx.MockTransport(fake))  # noqa: E731
    monkeypatch.setattr(X, "_new_client", mk)
    monkeypatch.setattr(C, "_new_client", mk)
    t = client.post(f"/tenants/{world.tenant_a}/admin/connectors/databento/test", headers=alice)
    assert t.json()["ok"] and t.json()["result"]["cme_futures"] is True and KEY not in t.text
    r = client.post(f"/tenants/{world.tenant_a}/trading/futures/bars", headers=alice,
                    json={"symbol": "MNQ", "start": "2026-11-01", "end": "2026-11-05",
                          "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 202, r.text
    w = Worker(maker, retry_base_seconds=0)
    w.run_once()
    assert not any(c.url.path.endswith("get_range") for c in fake.calls)  # not before approval
    ap = owner_db.scalar(select(Approval).where(Approval.run_id == uuid.UUID(r.json()["run_id"])))
    assert ap.category == "spend" and ap.amount_cents == 5 and "$0.0421" in ap.description
    client.post(f"/tenants/{world.tenant_a}/approvals/{ap.id}/decision", headers=alice,
                json={"decision": "approve"})
    w.run_once()
    run, tasks = run_state(owner_db, r.json()["run_id"])
    assert run.status == "succeeded", tasks[0].error
    out = tasks[0].output
    assert out["bars"] == 2 and out["cost_usd"] == 0.0421
    from jackson_api.artifacts import LocalArtifactStore
    from jackson_api.models import Artifact
    bars = json.loads(LocalArtifactStore().get(
        owner_db.get(Artifact, uuid.UUID(out["artifact_id"])).storage_key))["bars"]
    assert bars[0]["close"] == 21080.0 and bars[1]["close"] == 21120.0  # fixed-point scaled


def test_not_connected(client, world, alice, monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    monkeypatch.delenv("APEX7_RESULTS_URL", raising=False)
    r = client.post(f"/tenants/{world.tenant_a}/trading/futures/bars", headers=alice,
                    json={"start": "2026-11-01", "end": "2026-11-05",
                          "idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "DATABENTO_API_KEY" in r.text
    r = client.post(f"/tenants/{world.tenant_a}/trading/backtests/pull", headers=alice,
                    json={"idempotency_key": uuid.uuid4().hex})
    assert r.status_code == 409 and "APEX7_RESULTS_URL" in r.text
