# Jackson OS | Architecture and Body
Version 2.0 — 2026-11-25 | Proposed implementation specification

# Proposed stack
TypeScript monorepo, Next.js console, Python FastAPI orchestration and workers, PostgreSQL with pgvector, Redis for caching, Temporal for durable workflows, S3-compatible artifacts, Docker Compose locally. Choose managed deployment after load and security testing. These are proposed defaults, not existing infrastructure.
# Body and tools
Typed tool registry for HTTP APIs, browser automation (Playwright), isolated shell/code execution, file operations, screenshots, OCR when necessary, email, calendars, and media generation. Each tool declares schema, permissions, cost, timeout, idempotency and verification method. Use official APIs first; browser fallback only where permitted. No CAPTCHA bypass, credential theft or access-control evasion.
# Visual work
Stream sandbox browser screenshots or authorized remote desktop sessions to console. Display active task, agent, current URL, logs and artifacts. Accept text or voice commands and annotated screenshots; convert to structured intent. Pause, take over, resume or terminate. Redact secrets and sensitive fields from captures.
# Data contracts
Entities: Tenant, User, Business, Opportunity, AgentSpec, AgentInstance, SkillVersion, ToolGrant, WorkflowRun, Task, Artifact, Approval, Budget, LedgerEntry, AuditEvent, MemoryRecord. IDs, timestamps, tenant scope and status required. Task states: queued, running, waiting_approval, succeeded, failed, canceled. API: POST /commands, GET /runs/{id}, POST /approvals/{id}/decision, POST /runs/{id}/cancel, GET /events (SSE).
# Reliability
Use durable retries with backoff, idempotency keys, dead-letter handling, leases, concurrency quotas, encrypted secrets and scoped service accounts. Never put API keys in prompts or Git. Back up and test restore. Treat external pages and tool outputs as untrusted data.
