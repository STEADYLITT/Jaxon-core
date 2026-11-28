"""APEX-7 paper trading (ADR-047): real market data and a PAPER account at Alpaca.

- paper_snapshot: reads the paper account (equity, cash, today's P&L), positions, the
  last month's equity history and recent daily bars for the watched symbols -> a JSON
  artifact (type paper_account). The Trading KPIs ("Paper P&L") read the latest one.
- paper_order: one market order on the PAPER account only (the connector can't reach the
  live host). Paper money, so it's a SIMULATION action, but every order is recorded.
Live orders stay refused by the router and planner (restricted action).
"""

import json


def _put(ctx, payload: dict, meta: dict) -> str:
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


def snapshot_handler(ctx):
    from .connectors import broker as B
    from .worker import TaskResult

    inp = ctx.input or {}
    api = B.paper()
    acct, positions, hist = api.account(), api.positions(), api.history("1M")
    watch = [str(s) for s in (inp.get("symbols") or ["MNQ"])][:5]
    bars, notes = {}, []
    for s in watch:
        tradable, note = B.symbol(s)
        bars[tradable] = api.bars(tradable, "1Day", 30)
        if note:
            notes.append(note)
    meta = {"type": "paper_account", "source": "alpaca_paper", "equity": acct["equity"],
            "day_pl": acct["day_pl"], "month_pl": hist["pl"], "positions": len(positions)}
    aid = _put(ctx, {"account": acct, "positions": positions, "history": hist, "bars": bars,
                     "notes": notes}, meta)
    return TaskResult({"artifact_id": aid, **meta, "symbols": list(bars), "notes": notes,
                       "summary": f"Paper account: equity ${acct['equity']}, month P&L "
                                  f"${hist['pl']} (Alpaca paper, not real money)"})


def order_handler(ctx):
    from .connectors import broker as B
    from .models import RunEvent
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    sym, qty, side = str(inp.get("symbol") or ""), int(inp.get("qty") or 0), inp.get("side")
    ctx.authorize(Action(Category.SIMULATION, f"paper {side} {qty} {sym} (Alpaca paper)"))
    o = B.paper().paper_order(sym, qty, side, f"jackson-{ctx.task_id}")
    with ctx.db_session() as db:
        db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id, type="paper.order",
                        data=o))
        db.commit()
    return TaskResult({**o, "summary": f"Paper {side} {o['qty']} {o['symbol']}: {o['status']}"
                       + (f" ({o['note']})" if o.get("note") else "")})
