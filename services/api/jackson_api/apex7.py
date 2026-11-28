"""APEX-7 tasks (ADR-054): import real backtest/sim results and pull real futures bars."""

import json
import math

from .worker_errors import NonRetryableError


def _artifact(ctx, payload: dict, meta: dict) -> str:
    from .models import Artifact

    data = json.dumps(payload, indent=1, default=str).encode()
    key, sha, size = ctx.artifacts.put(ctx.tenant_id, data)
    with ctx.db_session() as db:
        art = Artifact(tenant_id=ctx.tenant_id, run_id=ctx.run_id, task_id=ctx.task_id,
                       kind="report", content_type="application/json", size_bytes=size,
                       sha256=sha, storage_key=key, meta=meta)
        db.add(art)
        db.flush()
        aid = str(art.id)
        db.commit()
    return aid


def import_handler(ctx):
    """Save one or more APEX-7 runs (posted, or pulled from APEX7_RESULTS_URL)."""
    from .connectors import apex7 as X
    from .worker import TaskResult

    inp = ctx.input or {}
    runs = X.pull_results()["runs"] if inp.get("pull") else [inp]
    saved = []
    for r in runs[:20]:
        symbol = str(r.get("symbol") or "MNQ")[:20]
        raw = X.parse_csv(r["csv"]) if r.get("csv") else list(r.get("trades") or [])
        trades = X.normalize(raw, symbol)
        if not trades:
            raise NonRetryableError("no usable trades: each needs pnl, or entry and exit "
                                    "prices")
        st = X.stats(trades)
        meta = {"type": "backtest", "source": str(r.get("source") or "APEX-7")[:60],
                "name": str(r.get("name") or "APEX-7 run")[:120], "symbol": symbol,
                "timeframe": str(r.get("timeframe") or "")[:20],
                "kind": "sim" if r.get("kind") == "sim" else "backtest",
                "skipped_trades": len(raw) - len(trades), **st}
        aid = _artifact(ctx, {"meta": meta, "trades": trades}, meta)
        saved.append({"artifact_id": aid, **meta})
    last = saved[-1]
    return TaskResult({"runs": saved, "summary": f"Imported {len(saved)} APEX-7 run(s): "
                       f"{last['trades']} trades, net ${last['net_pnl']:,.2f}, win rate "
                       f"{last['win_rate_pct']}%"})


def bars_handler(ctx):
    """Real CME futures bars from Databento: the cost is checked first and approved."""
    from .connectors import apex7 as X
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    api = X.databento()
    p = api.params(str(inp.get("symbol") or "MNQ"), str(inp.get("schema") or "ohlcv-1d"),
                   str(inp["start"]), str(inp["end"]))
    usd = api.cost_usd(p)
    est = max(1, math.ceil(usd * 100))
    desc = (f"Databento {p['symbols']} {p['schema']} {p['start']} to {p['end']}: cost "
            f"${usd:.4f} (Databento's own quote)")
    ctx.authorize(Action(Category.SPEND, desc, est, channel="market_data", always_ask=True))
    reservation = ctx.reserve_budget(est, desc)
    bars = api.bars(p)
    ctx.settle_spend(reservation, est, desc)
    meta = {"type": "futures_bars", "source": "databento", "symbols": p["symbols"],
            "schema": p["schema"], "start": p["start"], "end": p["end"], "bars": len(bars),
            "cost_usd": round(usd, 4)}
    aid = _artifact(ctx, {"meta": meta, "bars": bars}, meta)
    return TaskResult({"artifact_id": aid, **meta,
                       "summary": f"{len(bars)} {p['schema']} bars of {p['symbols']} "
                                  f"(${usd:.2f})"})
