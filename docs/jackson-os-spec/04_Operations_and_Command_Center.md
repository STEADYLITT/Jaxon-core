# Jackson OS | Operations and Command Center
Version 2.0 — 2026-11-25 | Proposed implementation specification

# Command interface
Commands: PLAN, EXECUTE, SIMULATE, REPORT, OPTIMIZE, BUILD, PAUSE, RESUME, STOP. Natural language maps to typed command with objective, business, budget, deadline, constraints and success criteria. EXECUTE authorizes only actions already within granted scope. Missing material parameters trigger clarification or safe defaults.
# Autonomy policy
Level 0 read-only; level 1 drafts and simulations; level 2 reversible sandbox changes; level 3 approved bounded production workflows. Require explicit approval for spending outside limits, public posting, outbound campaigns, payments, account creation, production changes, live trades and sensitive data access. Never delegate broader permissions than parent has.
# Owner dashboard
Portfolio P&L, cash and committed spend; opportunity board; agent roster and health; workflow timeline; live visual work; approvals inbox; artifacts; connector status; audit logs; global stop. Show actual vs estimated revenue separately. Global stop revokes new work and cancels interruptible runs; reconcile in-flight external effects.
# Metrics and controls
Track cost per run, verified success rate, failure rate, latency, approval wait, conversion, gross margin, refunds and customer acquisition cost. Budget reservations prevent concurrent overspend. Alert on repeated failures, anomalies and stale integrations. Daily reports summarize outcomes, blockers and next actions.
