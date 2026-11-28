"""Payout feeds (ADR-057): clip campaign payouts and creator earnings, read exactly as the
source reports them. Nothing is estimated: a row with no amount is skipped, never guessed.

Each line has one variable naming where its numbers come from:
- CLIP_PAYOUTS_SOURCE      clips:    an https JSON feed, or "import" (CSV/JSON uploads only)
- CREATOR_EARNINGS_SOURCE  creators: an https JSON feed, or "import"
with an optional Bearer token (CLIP_PAYOUTS_TOKEN / CREATOR_EARNINGS_TOKEN) for the feed.

A feed returns a list of payouts, or {"payouts"|"earnings"|"data"|"items": [...]}. Each
payout (JSON or a CSV row; column names are matched loosely):
  reference   the source's own id for the payout (else a hash of the row, so a re-import
              never double counts)
  subject     clips: the clip URL; creators: the creator's name
  platform    tiktok / youtube / instagram / x / onlyfans / fanvue / ...
  campaign    clip campaign or offer name
  views       views the payout was paid on (clips)
  amount      money in the currency's major unit (12.50), or amount_cents (1250)
  currency    ISO code, default USD
  paid_at     date or ISO timestamp the money was paid
"""

import csv
import hashlib
import io
import json
import math
import os
from datetime import UTC, datetime

import httpx

from ..worker_errors import NonRetryableError

LINES = {
    "clips": {"source": "CLIP_PAYOUTS_SOURCE", "token": "CLIP_PAYOUTS_TOKEN",
              "what": "clip payouts"},
    "creators": {"source": "CREATOR_EARNINGS_SOURCE", "token": "CREATOR_EARNINGS_TOKEN",
                 "what": "creator earnings"},
}
MAX_ROWS = 20_000

ALIASES = {
    "reference": ("reference", "id", "payout_id", "transaction_id", "txn_id", "ref"),
    "subject": ("subject", "clip_url", "url", "link", "video_url", "creator", "creator_name",
                "name", "talent", "model"),
    "platform": ("platform", "network", "site"),
    "campaign": ("campaign", "campaign_name", "offer", "bounty", "program"),
    "views": ("views", "view_count", "plays", "impressions"),
    "amount": ("amount", "payout", "earnings", "net", "net_earnings", "amount_usd", "paid",
               "revenue"),
    "amount_cents": ("amount_cents", "cents"),
    "currency": ("currency", "ccy"),
    "paid_at": ("paid_at", "date", "paid_on", "payout_date", "created_at", "period_end"),
}


def source(line: str) -> str:
    return (os.environ.get(LINES[line]["source"]) or "").strip()


def feed_url(line: str) -> str | None:
    s = source(line)
    return s if s.startswith("https://") else None


def configured(line: str) -> bool:
    s = source(line)
    return s == "import" or s.startswith("https://")


def _num(x) -> float | None:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def _pick(row: dict, key: str):
    low = {str(k).lower().strip().replace(" ", "_").replace("-", "_"): v
           for k, v in row.items()}
    for a in ALIASES[key]:
        v = low.get(a)
        if v not in (None, ""):
            return v
    return None


def _when(x) -> datetime | None:
    s = str(x or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        for fmt in ("%m/%d/%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                d = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def parse_csv(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))


def normalize(rows: list[dict], line: str) -> tuple[list[dict], int]:
    """Returns (payouts, skipped). A payout needs a subject and an amount >= 0."""
    out, skipped = [], 0
    for r in rows[:MAX_ROWS]:
        if not isinstance(r, dict):
            skipped += 1
            continue
        cents = _num(_pick(r, "amount_cents"))
        if cents is None:
            amt = _num(_pick(r, "amount"))
            cents = None if amt is None else amt * 100
        subject = str(_pick(r, "subject") or "").strip()
        if cents is None or cents < 0 or not subject:
            skipped += 1  # unknown or negative amount, or no clip/creator: never guessed
            continue
        views = _num(_pick(r, "views"))
        paid = _when(_pick(r, "paid_at"))
        ref = str(_pick(r, "reference") or "").strip()
        if not ref:
            raw = json.dumps({k: r[k] for k in sorted(r, key=str)}, default=str, sort_keys=True)
            ref = "h:" + hashlib.sha256(raw.encode()).hexdigest()[:40]
        out.append({
            "reference": ref[:200], "subject": subject[:300],
            "platform": (str(_pick(r, "platform") or "").strip().lower()[:32] or None),
            "campaign": (str(_pick(r, "campaign") or "").strip()[:200] or None),
            "views": int(views) if views is not None and views >= 0 else None,
            "amount_cents": int(round(cents)),
            "currency": (str(_pick(r, "currency") or "USD").strip().upper()[:3] or "USD"),
            "paid_at": paid.isoformat() if paid else None,
            "period": (paid or datetime.now(UTC)).strftime("%Y-%m"),
        })
    return out, skipped


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=60)


def pull(line: str) -> list[dict]:
    url = feed_url(line)
    cfg = LINES[line]
    if not url:
        raise NonRetryableError(f"set {cfg['source']} to the https address of your "
                                f"{cfg['what']} feed (or upload a CSV)")
    h = {"Accept": "application/json"}
    tok = (os.environ.get(cfg["token"]) or "").strip()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    with _new_client() as c:
        r = c.get(url, headers=h)
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()  # retryable
    if r.status_code >= 400:
        raise NonRetryableError(f"the {cfg['what']} feed answered HTTP {r.status_code}")
    try:
        body = r.json()
    except ValueError:
        raise NonRetryableError(f"the {cfg['what']} feed didn't return JSON") from None
    if isinstance(body, dict):
        for k in ("payouts", "earnings", "data", "items"):
            if isinstance(body.get(k), list):
                return body[k]
        raise NonRetryableError(f"the {cfg['what']} feed has no list of payouts")
    if isinstance(body, list):
        return body
    raise NonRetryableError(f"the {cfg['what']} feed has no list of payouts")
