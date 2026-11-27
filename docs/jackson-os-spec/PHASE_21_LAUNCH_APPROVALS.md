# Phase 21 — Launch approvals (one approval per launch)

Date: 2026-11-27 · Owner decision (Steady): one approval per launch, then Jackson runs it alone. Decision record: ADR-032 in [PHASE_01_GAP_REPORT.md](PHASE_01_GAP_REPORT.md#3-architecture-decisions-adrs).

## Plain English
- **Press Launch on an opportunity and approve once.** You set:
  - the total budget and a daily spend cap
  - how long it runs (14, 30 or 60 days)
  - the allowed channels: publish Shopify products, which social accounts, which email lists, and whether ad spend is allowed
  - kill rules, e.g. "stop if a sale costs more than $40" or "stop if no sales after 7 days"
- **Inside that scope Jackson acts on his own.** He publishes products, posts, emails the named lists, creates content and spends within the caps, with no approval per action.
- **Anything outside it still comes to you.** That covers more money than the caps, a new channel or account, payments, opening accounts and sensitive data. Live trades are always refused.
- **It stops itself when something looks wrong.** It auto-pauses on:
  - a spend spike: more than half the daily cap in one hour
  - bounce or complaint spikes
  - a refund spike
  - repeated platform errors

  A kill rule ends the launch. It also ends on its own when the time or the budget runs out. Every one of these is in the audit log and the daily report.
- **You stay in control.** You can pause, resume, edit (each edit is a new version, with before and after kept in the audit log) or revoke a launch at any time. Revoking cancels its work. Global Stop pauses every launch.
- **Jackson can't widen his own scope.** The database lets him propose a launch and stop one, nothing else. He can't change budgets or channels, can't restart a launch, and can't attach other work to a launch.

## How it works
- **`launches`:** the scope, set only by the owner via the API, plus status and version. **`launch_signals`:** sales, refunds, sends, bounces, complaints and platform errors, which drive the rules.
- **A run started from a launch carries its `launch_id`.** A trigger makes that link permanent from creation. Every action in that run goes through the policy engine, which asks `launches.covers()`:
  - is the launch active and inside its window?
  - is the channel or target allowed?
  - does the spend fit both the total budget and today's cap? (Spend decisions lock the launch row, so concurrent spends can't exceed the caps.)
- **In scope → allowed at level 3.** Out of scope → approvals inbox, with the reason. Spend in a launch run must fit the launch caps even if the business is at level 3.
- **Health check before every in-scope decision,** plus a periodic sweep in the worker (`LAUNCH_SWEEP_SECONDS`, default 60). The worker skips the tasks of paused or stopped launches.
- **Database guards:** content may be scheduled or published without a person's approval only when a covering, active launch of the same business allows that social account and the compliance review passed. The same rule applies to a campaign and its email list.

## API
- `POST /tenants/{id}/launches`: owner approves a launch with its scope.
- `POST …/launches/{id}/approve` and `/reject`: answer a launch Jackson proposed (handler `launch_propose`).
- `PATCH …/launches/{id}`: edit the scope.
- `POST …/launches/{id}/pause`, `/resume`, `/revoke`
- `POST …/launches/{id}/start`: Jackson's run for the launch.
- `POST …/launches/{id}/signals`: record a sale or refund by hand until a connector reports it.
- `GET …/launches` and `GET …/launches/{id}`: list launches, or one launch with metrics.

## Tests
- `test_launches.py`: 36 tests, including the console Launch button in real Chromium.
- Full suite: 462 passed.
- Mutation checks: 16 rules broken one at a time. Three slipped through at first: the paused-launch check, the business check and the health check before acting. Each now has a test that catches it. The remaining survivor is equivalent: a second check already refuses payments and account creation.
