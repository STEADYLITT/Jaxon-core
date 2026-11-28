"""APEX-7 results and futures market data (ADR-054).

Results: APEX-7's own backtest/sim trades come in one of two ways, and every number on the
Trading building is computed from them, never estimated:
- push: POST /tenants/{t}/trading/backtests with the trade list (JSON) or a CSV export
  (APEX-7, NinjaTrader, TradingView "List of trades"), or
- pull: APEX7_RESULTS_URL (an https endpoint APEX-7 publishes, returning the same JSON) with
  optional APEX7_RESULTS_TOKEN as a Bearer token.
Trade fields: entry_time, exit_time, side (long/short), qty, entry_price, exit_price,
optional pnl (cash, after fees) and fees. Without pnl, P&L = (exit - entry) * qty * point
value * (1 long / -1 short), with CME point values for the common index futures.

Market data: real CME futures bars (MNQ etc.) from Databento, because Alpaca has none.
- base https://hist.databento.com/v0, HTTP basic auth with the API key as the username
- GET  metadata.list_datasets                      -> ["GLBX.MDP3", ...] (free test)
- POST metadata.get_cost (form: dataset, symbols, schema, stype_in, start, end) -> USD float
- POST timeseries.get_range (same + encoding=json, compression=none, pretty_px=true,
  pretty_ts=true, map_symbols=true) -> one JSON record per line
  {"ts_event", "open", "high", "low", "close", "volume", "symbol"}; without pretty_px the
  prices are fixed-point integers (divide by 1e9)
- continuous front month: MNQ.c.0 with stype_in=continuous (not back-adjusted)
Every data pull shows its cost (from get_cost) and is a SPEND approved first.
"""

import csv
import io
import json
import math
import os
from datetime import datetime

import httpx

from ..worker_errors import NonRetryableError

POINT_VALUE = {"MNQ": 2.0, "NQ": 20.0, "MES": 5.0, "ES": 50.0, "MYM": 0.5, "YM": 5.0,
               "M2K": 5.0, "RTY": 50.0, "MCL": 100.0, "CL": 1000.0, "MGC": 10.0, "GC": 100.0}
DB = "https://hist.databento.com/v0"
SCHEMAS = ("ohlcv-1d", "ohlcv-1h", "ohlcv-1m")
MAX_TRADES = 20_000


def point_value(symbol: str) -> float:
    root = "".join(ch for ch in (symbol or "").upper().split(".")[0] if ch.isalpha())
    for k in sorted(POINT_VALUE, key=len, reverse=True):
        if root.startswith(k):
            return POINT_VALUE[k]
    return 1.0  # stocks/ETFs: P&L per share


def _num(x) -> float | None:
    try:
        v = float(str(x).replace("$", "").replace(",", "").strip())
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


CSV_ALIASES = {
    "entry_time": ("entry_time", "entry time", "date/time", "entry date", "entry"),
    "exit_time": ("exit_time", "exit time", "exit date", "exit"),
    "side": ("side", "type", "market pos.", "direction", "signal"),
    "qty": ("qty", "quantity", "contracts", "size"),
    "entry_price": ("entry_price", "entry price", "price entry"),
    "exit_price": ("exit_price", "exit price", "price exit"),
    "pnl": ("pnl", "profit", "net p&l", "net profit", "p&l", "profit usd"),
    "fees": ("fees", "commission", "commissions"),
}


def parse_csv(text: str) -> list[dict]:
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return []
    cols = {c.lower().strip(): c for c in rows[0]}
    pick = {k: next((cols[a] for a in names if a in cols), None)
            for k, names in CSV_ALIASES.items()}
    return [{k: (r.get(c) if c else None) for k, c in pick.items()} for r in rows]


def normalize(trades: list[dict], symbol: str) -> list[dict]:
    pv = point_value(symbol)
    out = []
    for t in trades[:MAX_TRADES]:
        side = str(t.get("side") or "long").lower()
        sign = -1 if ("short" in side or side.startswith("sell")) else 1
        qty = _num(t.get("qty")) or 1.0
        pnl = _num(t.get("pnl"))
        ep, xp = _num(t.get("entry_price")), _num(t.get("exit_price"))
        if pnl is None:
            if ep is None or xp is None:
                continue  # can't know this trade's result: left out, never guessed
            pnl = (xp - ep) * qty * pv * sign - (_num(t.get("fees")) or 0.0)
        out.append({"entry_time": t.get("entry_time"), "exit_time": t.get("exit_time"),
                    "side": "short" if sign < 0 else "long", "qty": qty,
                    "entry_price": ep, "exit_price": xp, "pnl": round(pnl, 2)})
    return out


def stats(trades: list[dict]) -> dict:
    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    equity, peak, mdd = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    gp, gl = sum(wins), -sum(losses)
    return {"trades": len(pnls), "wins": len(wins), "losses": len(losses),
            "win_rate_pct": round(100 * len(wins) / len(pnls), 1) if pnls else None,
            "net_pnl": round(sum(pnls), 2), "gross_profit": round(gp, 2),
            "gross_loss": round(gl, 2),
            "profit_factor": round(gp / gl, 2) if gl else None,
            "max_drawdown": round(mdd, 2),
            "avg_win": round(gp / len(wins), 2) if wins else None,
            "avg_loss": round(-gl / len(losses), 2) if losses else None,
            "expectancy": round(sum(pnls) / len(pnls), 2) if pnls else None}


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=60)


def pull_results() -> dict:
    url = os.environ.get("APEX7_RESULTS_URL", "")
    if not url.startswith("https://"):
        raise NonRetryableError("set APEX7_RESULTS_URL (the https address APEX-7 publishes its "
                                "results at)")
    h = {"Accept": "application/json"}
    if os.environ.get("APEX7_RESULTS_TOKEN"):
        h["Authorization"] = f"Bearer {os.environ['APEX7_RESULTS_TOKEN']}"
    with _new_client() as c:
        r = c.get(url, headers=h)
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()
    if r.status_code >= 400:
        raise NonRetryableError(f"APEX-7 results endpoint answered HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        raise NonRetryableError("APEX-7 results endpoint didn't return JSON") from None
    runs = body if isinstance(body, list) else [body]
    return {"runs": [x for x in runs if isinstance(x, dict) and (x.get("trades") or x.get("csv"))]}


def databento_configured() -> bool:
    return bool(os.environ.get("DATABENTO_API_KEY"))


def _db_check(r: httpx.Response, what: str):
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()
    if r.status_code >= 400:
        try:
            msg = r.json().get("detail")
        except ValueError:
            msg = r.text[:200]
        raise NonRetryableError(f"Databento refused the {what}: {str(msg)[:300]}")
    return r


class Databento:
    def __init__(self, key: str, client: httpx.Client | None = None):
        self._auth = (key, "")
        self._client = client or _new_client()

    def datasets(self) -> list[str]:
        return _db_check(self._client.get(f"{DB}/metadata.list_datasets", auth=self._auth),
                         "test call").json()

    @staticmethod
    def params(symbol: str, schema: str, start: str, end: str) -> dict:
        if schema not in SCHEMAS:
            raise NonRetryableError(f"schema must be one of {SCHEMAS}")
        sym = symbol.strip().upper()
        if not sym.endswith((".C.0", ".V.0", ".N.0")):
            sym = f"{sym}.c.0"
        sym = sym.replace(".C.", ".c.").replace(".V.", ".v.").replace(".N.", ".n.")
        for d in (start, end):
            datetime.fromisoformat(d)  # raises on a bad date
        return {"dataset": "GLBX.MDP3", "symbols": sym, "schema": schema,
                "stype_in": "continuous", "start": start, "end": end}

    def cost_usd(self, p: dict) -> float:
        return float(_db_check(self._client.post(f"{DB}/metadata.get_cost", data=p,
                                                 auth=self._auth), "cost check").json())

    def bars(self, p: dict) -> list[dict]:
        r = _db_check(self._client.post(f"{DB}/timeseries.get_range", auth=self._auth, data={
            **p, "encoding": "json", "compression": "none", "pretty_px": "true",
            "pretty_ts": "true", "map_symbols": "true"}), "bars")
        out = []
        for line in r.text.splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            px = {k: _num(rec.get(k)) for k in ("open", "high", "low", "close")}
            if any(v is not None and abs(v) > 1e7 for v in px.values()):  # fixed-point
                px = {k: (v / 1e9 if v is not None else None) for k, v in px.items()}
            ts = rec.get("ts_event") or (rec.get("hd") or {}).get("ts_event")
            out.append({"t": ts, **px, "v": rec.get("volume"), "symbol": rec.get("symbol")})
        return out


def databento() -> Databento:
    if not databento_configured():
        raise NonRetryableError("set DATABENTO_API_KEY in Railway for futures data")
    return Databento(os.environ["DATABENTO_API_KEY"])
