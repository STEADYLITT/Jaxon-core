"""Trading data for APEX-7 via Alpaca: market data and a PAPER trading account (ADR-047).

Paper only, by construction: the trading host is hard-coded to
https://paper-api.alpaca.markets and there is no setting that changes it. Live orders stay
refused (router + planner) until a live broker connector with daily loss caps, a kill switch
and per-order approval exists.

Checked against docs.alpaca.markets (2026-11-27):
- headers APCA-API-KEY-ID / APCA-API-SECRET-KEY (paper keys differ from live keys; a paper
  key on the live host, or the reverse, answers 403)
- GET  {paper}/v2/account                 -> equity, last_equity, cash, buying_power, status
- GET  {paper}/v2/positions               -> [{symbol, qty, market_value, unrealized_pl}]
- GET  {paper}/v2/account/portfolio/history?period=1M&timeframe=1D -> {timestamp[], equity[],
  profit_loss[], base_value}
- POST {paper}/v2/orders {symbol, qty, side, type, time_in_force, client_order_id}
- GET  https://data.alpaca.markets/v2/stocks/{symbol}/bars?timeframe=1Day&feed=iex
  -> {bars: [{t, o, h, l, c, v}], next_page_token}  (paper-only accounts get IEX data)
- Alpaca has no futures: APEX-7's MNQ (Micro Nasdaq-100) can't be traded or charted here.
  QQQ (a Nasdaq-100 ETF) is the closest proxy for paper testing.
"""

import os
import re

import httpx

from ..worker_errors import NonRetryableError

PAPER = "https://paper-api.alpaca.markets"  # never the live host
DATA = "https://data.alpaca.markets"
FUTURES = re.compile(r"^/?(M?NQ|M?ES|M?YM|RTY|M2K|CL|MCL|GC|MGC)[A-Z]?\d*$", re.I)
PROXY = {"MNQ": "QQQ", "NQ": "QQQ", "MES": "SPY", "ES": "SPY", "MYM": "DIA", "YM": "DIA",
         "M2K": "IWM", "RTY": "IWM"}
MAX_PAPER_QTY = 1000


def configured() -> bool:
    return bool(os.environ.get("ALPACA_PAPER_KEY_ID") and
                os.environ.get("ALPACA_PAPER_SECRET_KEY"))


def symbol(s: str) -> tuple[str, str | None]:
    """(tradable symbol, note). Futures map to their ETF proxy, with a note saying so."""
    s = (s or "").strip().upper().lstrip("/")
    if not re.fullmatch(r"[A-Z.]{1,10}\d*", s):
        raise NonRetryableError(f"not a stock/ETF symbol: {s!r}")
    root = re.sub(r"[FGHJKMNQUVXZ]\d{1,2}$", "", s) if FUTURES.match(s) else s
    if root in PROXY:
        return PROXY[root], (f"{s} is a CME future; Alpaca has no futures, so the paper test "
                             f"uses {PROXY[root]} as a proxy")
    if FUTURES.match(s):
        raise NonRetryableError(f"{s} is a future and Alpaca has no futures or a proxy for it")
    return s, None


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=20)


def _check(r: httpx.Response, what: str):
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code == 403:
        raise NonRetryableError("Alpaca refused the key on the PAPER host: use the paper "
                                "account's keys (live keys are never used)")
    if r.status_code >= 400:
        msg = body.get("message") if isinstance(body, dict) else None
        raise NonRetryableError(f"Alpaca refused the {what}: {str(msg or r.status_code)[:300]}")
    return body


def _f(x):
    try:
        return round(float(x), 2)
    except (TypeError, ValueError):
        return None


class AlpacaPaper:
    def __init__(self, key_id: str, secret: str, client: httpx.Client | None = None):
        self._h = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self._client = client or _new_client()

    def account(self) -> dict:
        a = _check(self._client.get(f"{PAPER}/v2/account", headers=self._h), "account")
        eq, last = _f(a.get("equity")), _f(a.get("last_equity"))
        return {"account": a.get("account_number"), "status": a.get("status"), "equity": eq,
                "cash": _f(a.get("cash")), "buying_power": _f(a.get("buying_power")),
                "day_pl": round(eq - last, 2) if eq is not None and last is not None else None,
                "paper": True}

    def positions(self) -> list[dict]:
        rows = _check(self._client.get(f"{PAPER}/v2/positions", headers=self._h), "positions")
        return [{"symbol": p.get("symbol"), "qty": _f(p.get("qty")),
                 "market_value": _f(p.get("market_value")),
                 "unrealized_pl": _f(p.get("unrealized_pl"))}
                for p in rows if isinstance(p, dict)] if isinstance(rows, list) else []

    def history(self, period: str = "1M") -> dict:
        h = _check(self._client.get(f"{PAPER}/v2/account/portfolio/history", headers=self._h,
                                    params={"period": period, "timeframe": "1D"}), "history")
        eq = [x for x in h.get("equity") or [] if x is not None]
        pl = [x for x in h.get("profit_loss") or [] if x is not None]
        return {"points": len(eq), "start_equity": _f(eq[0]) if eq else None,
                "end_equity": _f(eq[-1]) if eq else None,
                "pl": round(sum(pl), 2) if pl else None, "base_value": _f(h.get("base_value"))}

    def bars(self, sym: str, timeframe: str = "1Day", limit: int = 100) -> list[dict]:
        tradable, _ = symbol(sym)
        b = _check(self._client.get(f"{DATA}/v2/stocks/{tradable}/bars", headers=self._h,
                                    params={"timeframe": timeframe, "limit": min(limit, 1000),
                                            "feed": "iex", "adjustment": "all"}), "bars")
        return [{"t": x.get("t"), "o": x.get("o"), "h": x.get("h"), "l": x.get("l"),
                 "c": x.get("c"), "v": x.get("v")} for x in b.get("bars") or []]

    def paper_order(self, sym: str, qty: int, side: str, client_order_id: str) -> dict:
        tradable, note = symbol(sym)
        if side not in ("buy", "sell"):
            raise NonRetryableError("side must be buy or sell")
        if not 1 <= int(qty) <= MAX_PAPER_QTY:
            raise NonRetryableError(f"paper qty must be 1..{MAX_PAPER_QTY}")
        o = _check(self._client.post(f"{PAPER}/v2/orders", headers=self._h, json={
            "symbol": tradable, "qty": str(int(qty)), "side": side, "type": "market",
            "time_in_force": "day", "client_order_id": client_order_id[:48]}), "paper order")
        return {"order_id": o.get("id"), "status": o.get("status"), "symbol": tradable,
                "qty": int(qty), "side": side, "paper": True, "note": note}


def paper() -> AlpacaPaper:
    if not configured():
        raise NonRetryableError("Alpaca paper isn't connected: set ALPACA_PAPER_KEY_ID and "
                                "ALPACA_PAPER_SECRET_KEY in Railway")
    return AlpacaPaper(os.environ["ALPACA_PAPER_KEY_ID"], os.environ["ALPACA_PAPER_SECRET_KEY"])
