# ADR-047: APEX-7 on an Alpaca PAPER account (market data + paper orders; live refused)

**Status:** accepted, 2026-11-28

## Decision
- **Paper only, by construction:**
  - The trading host is hard-coded to `https://paper-api.alpaca.markets` and no setting changes it.
  - A test asserts the live host appears nowhere in the connector.
  - Paper keys are separate from live keys. A live key on the paper host gets 403, and Jackson's error says to use the paper account's keys.
- **Auth:** `APCA-API-KEY-ID` and `APCA-API-SECRET-KEY` headers.
- **Calls:**
  - `GET /v2/account`: also the connection test.
  - `GET /v2/positions`
  - `GET /v2/account/portfolio/history?period=1M&timeframe=1D`
  - `GET data.alpaca.markets/v2/stocks/{s}/bars?feed=iex` (paper-only accounts get IEX data)
  - `POST /v2/orders` with a market order, `time_in_force=day` and a `client_order_id`
- **Futures:** Alpaca has no futures. MNQ/NQ map to QQQ, MES/ES to SPY, MYM/YM to DIA and M2K/RTY to IWM. Each result carries a note saying the proxy was used. Other futures, such as CL or GC, are refused.
- **Tasks:**
  - **`paper_snapshot`** (the "Paper Account Reporter", Trading → Performance Reports): saves a `paper_account` artifact.
  - **`paper_order`** (the "Paper Trader", Trading → Backtests & Sim):
    - It is a `SIMULATION` action, so it needs approval below autonomy level 1.
    - Quantity is limited to 1 to 1,000.
    - Each order is recorded as a `paper.order` run event.
- **KPIs (Trading):**
  - "Paper P&L, last month (Alpaca paper)" and "Paper equity" come from the latest snapshot. With no snapshot they show no data.
  - "Live orders placed" is always 0.
- **Live trading:** unchanged. `LIVE_TRADE` is in `NEVER`, and the router and planner refuse live-order commands.

## Without keys
`/trading/paper/*` answers 409 and names `ALPACA_PAPER_KEY_ID` and `ALPACA_PAPER_SECRET_KEY`.
