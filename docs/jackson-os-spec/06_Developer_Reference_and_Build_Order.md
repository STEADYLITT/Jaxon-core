# Jackson OS | Developer Reference and Build Order
Version 2.0 — 2026-11-25 | Proposed implementation specification

# Repository layout
apps/console; services/api; services/workers; packages/contracts; packages/policy; packages/connectors; skills/; agents/; workflows/; infra/; tests/; docs/. Maintain README, ADRs, environment example, migration scripts and CI. Inspect existing jaxon-os repo before changing it; preserve working code.
# Implementation sequence
01 inventory repo and write gap report; 02 scaffold and CI; 03 auth and tenant isolation; 04 database and migrations; 05 typed commands and run API; 06 durable workflow engine; 07 policy, approvals and budget ledger; 08 model adapters; 09 memory and provenance; 10 tool registry and sandbox; 11 visual browser session; 12 agent registry and factory; 13 opportunity research; 14 product economics; 15 Shopify draft connector; 16 content and virtual influencer workflow; 17 website factory; 18 CRM and compliant outreach; 19 portfolio dashboard and reporting; 20 security, recovery and end-to-end pilot. For each phase: plan, implement, test, document, commit, report evidence and blockers. Do not skip failing tests.
# Acceptance tests
Cross-tenant access denied; unapproved spend blocked; repeated request does not duplicate external action; failed worker resumes safely; prompt injection cannot elevate grants; agent cannot spawn beyond quota; cancellation prevents new tasks; secrets absent from logs; browser session visible; memory cites source; sandbox Shopify draft verified; website preview passes tests; content remains draft until approved.
# First vertical slice
Owner enters: Find a product opportunity and build a draft store. Research agent returns evidence; finance calculates margin; planner requests approval for any paid action; builder creates draft in sandbox; QA verifies; dashboard shows artifact, cost and run history. No public launch required. This proves brain, body, agent factory and command center together.
# Integration prerequisites
Owner must provide authorized credentials, accounts, API scopes, billing limits and hosting choices. Use mocks until credentials exist, label mocks clearly. Verify current provider documentation before implementing each connector. No live integrations or existing repository state verified by this specification.
