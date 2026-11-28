# ADR-054: APEX-7 results and real futures market data

**Status:** accepted, 2026-11-28

## Decision
**APEX-7 results (`apex7_import`, the "Results Recorder", Trading → Backtests & Sim)**

How results come in:
- **Upload:** `POST /trading/backtests` takes JSON trades or a CSV. Columns from APEX-7, NinjaTrader and TradingView exports are mapped by alias.
- **Pull:** `POST /trading/backtests/pull` reads `APEX7_RESULTS_URL` (https only), using an optional Bearer `APEX7_RESULTS_TOKEN`.

How each trade's P&L is worked out:
- Use the trade's own `pnl` if it has one.
- Otherwise P&L = (exit − entry) × qty × point value × direction − fees.
- CME point values: MNQ 2, NQ 20, MES 5, ES 50, MYM 0.5, YM 5, M2K 5, RTY 50, MCL 100, CL 1000, MGC 10, GC 100. Stocks use 1.
- A trade with neither a P&L nor both prices is **skipped and counted**, never guessed.

Stats computed:
- trades, wins and losses
- win rate, net P&L, gross profit and gross loss
- profit factor, max drawdown (on the cumulative P&L curve)
- average win and loss, expectancy

Each run is saved as a `backtest` artifact (kind `backtest` or `sim`).

KPIs (Trading):
- "Backtests recorded (APEX-7)"
- "Latest backtest net P&L"
- "Latest backtest win rate"
- With no data these show nothing.

**Futures data (`futures_bars`, the "Market Data Desk", Trading → Research)**
- Databento Historical, with the API key as the basic-auth username.
- Test: `metadata.list_datasets`, which is free.
- Before any pull, `metadata.get_cost` with the same parameters returns Databento's USD quote. It becomes a `SPEND`: `always_ask`, rounded up to the cent, minimum 1¢.
- After approval, `timeseries.get_range` is called with `dataset=GLBX.MDP3`, `symbols=<ROOT>.c.0`, `stype_in=continuous`, `encoding=json`, `pretty_px` and `pretty_ts`.
- If a record still carries fixed-point prices, they are scaled by 1e-9.
- The bars are saved as a `futures_bars` artifact.
- Bars come from the calendar front month and are not back-adjusted.

**Live trading** is unchanged: still refused.

## Variables
- `APEX7_RESULTS_URL` and `APEX7_RESULTS_TOKEN` (both optional) replace the placeholder `APEX7_RESULTS_SOURCE`.
- `DATABENTO_API_KEY`
