# ADR-039: Jackson is the CEO of all of Steady's business lines

**Status:** accepted, 2026-11-27

## Decision
The City's generic departments are replaced by Steady's **8 business lines**. Each line is one building with its own:
- floors and agents
- playbook
- KPIs
- budget
- connectors
- launch template

There is one registry, `jackson_api/lines.py`, and it extends what exists rather than duplicating it.

| Line | Business (seeded) | Built-in agents placed here | Proposed agents (drafts, Steady approves) |
|---|---|---|---|
| R&D / New Ideas | APEX-7 (research) | Research Scout | trend-scout |
| Steady Trades University | Steady Trades University, CSW Trades | Content Writer, Social Publisher, Outreach Writer/Sender | stu-funnel-writer |
| Agency | Steady Ventures | Site Builder, Site Launcher | client-reporter |
| Clip Farming | Clip Farming (new) | none yet | clip-scout, clip-captioner |
| Creator Agency | Creator Agency (new) | none yet | creator-planner, caption-drafter |
| AI Influencers | AI Influencers (new) | Video Producer | persona-planner |
| E-commerce | Shopify Store | Store Builder, Shop Publisher | product-researcher |
| Finance & Ops | — (cross-line) | Daily Reporter, Agent Factory, Agent Evaluator | — |

## Reused, not duplicated
- **businesses:** gain a `line` column (owner can move one: `PATCH /tenants/{t}/businesses/{id}/line`).
- **Missing roles:** they become AgentSpec **drafts** (`created_by=factory`, `source_ref=line:<key>`). Nothing runs until Steady validates, evaluates and activates them through the existing agents API. A line with only proposed agents is an empty, "not staffed" lot.
- **launches:** unchanged. One approval per launch, per business. Each line only names the scope its launches usually need.
- **revenue:** only from real sources. Steady Trades University counts Whop only; E-commerce counts Shopify.

## New
- **`line_playbooks`:** versioned steps and rules plus a monthly budget per line. Edits are owner/admin only and audited.
- **Lines API:**
  - `GET /tenants/{t}/lines`
  - `GET /tenants/{t}/lines/{key}`: KPIs from real rows; anything without a source is `null` with the reason, never estimated. Also connectors (LIVE/MOCK, including "not built yet" ones with their variable), real and proposed agents, playbook, budget vs. spend this month, and the launch template.
  - `PATCH /tenants/{t}/lines/{key}/playbook`
- **Command-bar routing covers every line:**
  1. The router finds the line from its keywords. On a tie, a specific line beats R&D and Finance.
  2. It then picks an agent:
     - an **active** AgentSpec of that line that matches the command
     - Content Writer for posts
     - Daily Reporter for briefings
     - Research Scout when the command includes source links
  3. Otherwise Jaxon keeps the command. The plan is the draft, and the reason names the proposed agent that would take it and the Railway variables that would make the line real.
  4. A line whose connectors are MOCK is still planned and drafted, and the reason says which keys are missing.
  5. The routed run gets the line's business.
- **Explicit-content refusal:** these commands are refused before any planning or model call, for every line.
