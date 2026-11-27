# ADR-039: Jackson is the CEO of all of Steady's businesses (15 departments)

**Status:** accepted, 2026-11-27 (expanded from 8 business lines to the full scope)

## Decision
The City's generic departments are replaced by Steady's **15 departments**. Each one is a building with its own:
- floors and agents
- playbook (the core loop plus department rules)
- KPIs
- budget
- connectors
- launch template
- restricted actions

There is one registry, `jackson_api/lines.py`, and it extends what exists rather than duplicating it.

Every department runs the same **core loop**:
1. discover, with evidence
2. work out unit economics
3. propose an experiment (a Launch)
4. Steady approves once
5. build in a sandbox
6. QA
7. publish inside the approved scope
8. market and measure
9. iterate or kill
10. write the lesson to memory, with sources

| # | Department | Seeded businesses | Built-in agents | Proposed agents (drafts) |
|---|---|---|---|---|
| 1 | R&D / Opportunities | — | Research Scout | trend-scout |
| 2 | Course & Education Factory | STU, CSW Trades, Amazon University, AI Wealth Academy, Steady AI, Make It | — | course-architect, lesson-writer |
| 3 | Marketing (all companies) | Atom Labs, Steady Growth OS, SHRED 90 | Content Writer, Social Publisher, Outreach Writer/Sender | funnel-writer |
| 4 | Agency | Steady Ventures | — | client-reporter |
| 5 | Sales & Lead Gen | — | — | lead-researcher, call-scripter |
| 6 | App / SaaS / Web Factory | GOTTINK / InkFlow CRM | Site Builder, Site Launcher | web-developer, qa-tester |
| 7 | Content & Clip Farming | Clip Farming | — | clip-scout, clip-captioner |
| 8 | Creator Agency | Creator Agency | — | creator-planner, caption-drafter |
| 9 | AI Influencers | AI Influencers | Video Producer | persona-planner |
| 10 | E-commerce & Reselling | Shopify Store | Store Builder, Shop Publisher | product-researcher |
| 11 | Community | — | — | community-manager |
| 12 | Trading | APEX-7 | — | backtest-runner |
| 13 | Real Estate | — | — | underwriter |
| 14 | Legal & Admin | — | — | contract-drafter |
| 15 | Finance & Ops | — (all departments) | Daily Reporter, Agent Factory, Agent Evaluator | — |

## Reused, not duplicated
- **businesses:** gain a `line` column. The owner can move one with `PATCH /tenants/{t}/businesses/{id}/line`.
- **Missing roles:** they become AgentSpec **drafts** (`created_by=factory`, `source_ref=line:<key>`, empty tool scope, so level 1: drafts only).
  - Nothing runs until Steady validates, evaluates and activates them.
  - Agents can't create agents.
  - The total number of proposed agents is capped (`MAX_PROPOSED_AGENTS = 40`, vol. 05).
  - A department with only proposed agents is an empty "not staffed" lot.
- **launches:** unchanged. One approval per launch. Restricted actions (vol. 04) are never inside a launch.
- **revenue:** only from real sources (Whop, Shopify, referenced entries).

## Restricted actions
- **Trading:** live orders on real accounts always need Steady's explicit approval, with daily loss caps and a kill switch.
  - Until a broker connector with those controls exists, any live-order command is **refused before planning**, and the reason names those controls.
  - ADR-011 (no live trades) stands.
- **Legal:** drafts are always routed to Steady and never filed or signed automatically.
- **Real estate:** research and simulation only until Steady approves.
- **Creators and influencers:**
  - Explicit content is refused before any planning or model call.
  - AI personas are always disclosed: no impersonation, no fake engagement.

## New
- **`line_playbooks`:** versioned steps and rules plus a monthly budget per department. Edits are owner/admin only and audited.
- **Departments API:**
  - `GET /tenants/{t}/lines`
  - `GET /tenants/{t}/lines/{key}`: KPIs from real rows; anything without a source is `null` with the reason.
  - `PATCH /tenants/{t}/lines/{key}/playbook`
- **Command-bar routing covers all 15:**
  1. **Pick the department:** keywords match at the start of a word, more matching words win, and on a tie a specific department beats R&D, Marketing and Finance.
  2. **Pick the agent**, in this order:
     - an active AgentSpec of that department that matches the command
     - Content Writer for posts
     - Daily Reporter for briefings
     - Research Scout when the command includes source links
  3. **Otherwise Jaxon keeps it.** The plan is the draft, and the reason names the proposed agent and the missing Railway variables.
- **City:** the ring is wider (radius 36) and the camera and fog fit it on desktop and phone. On phones, empty lots drop their labels (the List view names them).

Variables per department: [`docs/LINES_AND_KEYS.md`](../LINES_AND_KEYS.md).
