"""Canonical tables for phases 03-04 (ADR-005, ADR-010, ADR-012, ADR-013).

Only entities with behaviour exist here. The remaining spec entities
(Opportunity, AgentSpec, Approval, Budget, LedgerEntry, MemoryRecord, ...)
are added by the phase that gives them behaviour.

Every tenant-scoped table carries tenant_id and is protected by Postgres
row-level security (see migrations/versions/0001_initial.py).
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

ROLES = ("owner", "admin", "member", "viewer")
WRITE_ROLES = frozenset({"owner", "admin"})


class Base(DeclarativeBase):
    pass


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Tenant(Base):
    __tablename__ = "tenants"
    id: Mapped[uuid.UUID] = _pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    # Global stop (spec vol. 04, phase 19). While set, no new runs and no task may start.
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stopped_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    stop_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class User(Base):
    """A human identity. Linked to the hosted auth provider by `auth_subject` (JWT `sub`)."""

    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    auth_subject: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Membership(Base):
    """User <-> Tenant with a role. A user may belong to several tenants (agency clients)."""

    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id"),
        CheckConstraint("role IN ('owner','admin','member','viewer')", name="ck_membership_role"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Business(Base):
    __tablename__ = "businesses"
    __table_args__ = (
        # Idempotent imports (ADR-008): one row per external record per tenant.
        UniqueConstraint("tenant_id", "source_system", "source_id"),
        CheckConstraint("autonomy_level BETWEEN 0 AND 3", name="ck_business_autonomy"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    # Spec vol. 04 autonomy level 0-3; only the owner can change it (phase 07).
    autonomy_level: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    source_system: Mapped[str | None] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AuditEvent(Base):
    """Append-only record of who did what (the app DB role has no UPDATE/DELETE here)."""

    __tablename__ = "audit_events"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    data: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="recorded")
    created_at: Mapped[datetime] = _created()


# --- Phase 05: commands, runs, tasks, run events --------------------------------

TASK_STATE_CHECK = (
    "status IN ('queued','running','waiting_approval','succeeded','failed','canceled')"
)


class WorkflowRun(Base):
    """One typed command (vol. 04) and its lifecycle. Executed by workers from phase 06."""

    __tablename__ = "workflow_runs"
    __table_args__ = (
        # Idempotency (vol. 06): one run per (tenant, idempotency key).
        UniqueConstraint("tenant_id", "idempotency_key"),
        CheckConstraint(TASK_STATE_CHECK, name="ck_workflow_run_status"),
        CheckConstraint("budget_cents IS NULL OR budget_cents >= 0", name="ck_run_budget"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    command_type: Mapped[str] = mapped_column(String(16), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    budget_cents: Mapped[int | None] = mapped_column(BigInteger)
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    constraints: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    success_criteria: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list
    )
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Set when the run works under an owner's Launch approval (phase 21). Fixed at insert.
    launch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("launches.id", use_alter=True, name="fk_runs_launch"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        CheckConstraint(TASK_STATE_CHECK, name="ck_task_status"),
        Index("ix_tasks_claim", "status", "next_attempt_at"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=False, index=True
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued")
    # --- Durable execution (phase 06, ADR-006) ---
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_owner: Mapped[str | None] = mapped_column(String(200))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    output: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # --- Agent delegation (phase 12, ADR-022) ---
    parent_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    agent_spec_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agent_specs.id"))
    # None = no extra restriction (still limited by grants); a list = only these tools.
    tool_scope: Mapped[list | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    input: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class RunEvent(Base):
    """Append-only run history; the monotonically increasing id is the SSE event id."""

    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=False, index=True
    )
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    data: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = _created()


# --- Phase 07: approvals and budget ledger ------------------------------------------


class Approval(Base):
    """A restricted action waiting for (or decided by) a human owner/admin.

    The worker role can only INSERT and SELECT here, so no agent can approve anything.
    """

    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("task_id", "fingerprint"),
        CheckConstraint("status IN ('pending','approved','rejected')", name="ck_approval_status"),
        CheckConstraint("amount_cents >= 0", name="ck_approval_amount"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)  # why policy asked
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class LedgerEntry(Base):
    """Append-only money movements per run. Exposure = sum(reserve) - sum(release)."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        CheckConstraint("kind IN ('reserve','commit','release')", name="ck_ledger_kind"),
        CheckConstraint("amount_cents >= 0", name="ck_ledger_amount"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("ledger_entries.id"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = _created()


# --- Phase 08: model usage metering ---------------------------------------------------


class ModelUsage(Base):
    """One row per model call. Append-only. Cost in micro-dollars (1e-6 USD)."""

    __tablename__ = "model_usage"
    __table_args__ = (
        CheckConstraint("mode IN ('live','mock')", name="ck_model_usage_mode"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    profile: Mapped[str] = mapped_column(String(32), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd_micros: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    request_id: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = _created()


# --- Phase 09: memory with provenance --------------------------------------------------

MEMORY_KINDS = ("preference", "business_fact", "decision", "project_state", "tool_result",
                "lesson")
EPISTEMIC = ("fact", "inference", "hypothesis")
MEMORY_SCOPES = ("tenant", "owner_only")
MEMORY_STATUSES = ("active", "pending_confirmation", "superseded", "rejected")


class MemoryRecord(Base):
    """Spec vol. 01 "Memory": provenance, timestamps, access scope, retention, epistemic status.

    Corrections never edit in place: a new version supersedes the old one, so the history
    survives. Deletion removes a record and all its versions for real.
    """

    __tablename__ = "memory_records"
    __table_args__ = (
        CheckConstraint(f"kind IN {MEMORY_KINDS}", name="ck_memory_kind"),
        CheckConstraint(f"epistemic IN {EPISTEMIC}", name="ck_memory_epistemic"),
        CheckConstraint(f"access_scope IN {MEMORY_SCOPES}", name="ck_memory_scope"),
        CheckConstraint(f"status IN {MEMORY_STATUSES}", name="ck_memory_status"),
        CheckConstraint("impact IN ('low','high')", name="ck_memory_impact"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_memory_confidence"),
        Index("ix_memory_fts", text("to_tsvector('english', content)"), postgresql_using="gin"),
        Index("ix_memory_key", "tenant_id", "key"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    epistemic: Mapped[str] = mapped_column(String(16), nullable=False)
    key: Mapped[str | None] = mapped_column(String(200))  # subject, for conflict detection
    content: Mapped[str] = mapped_column(Text, nullable=False)
    impact: Mapped[str] = mapped_column(String(8), nullable=False, default="low")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)  # owner|run|tool|import
    source_ref: Mapped[str | None] = mapped_column(String(500))  # run/task id, URL, import id
    source_detail: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )
    access_scope: Mapped[str] = mapped_column(String(16), nullable=False, default="tenant")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_records.id", ondelete="SET NULL")
    )
    created_by_user: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_by_agent: Mapped[str | None] = mapped_column(String(200))
    retain_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# --- Phase 10: tool registry grants and call log -----------------------------------------


class ToolGrant(Base):
    """Owner permission for a tool, tenant-wide (business_id NULL) or for one business.

    Only the API (owner) writes grants; agents can read them but never create one.
    """

    __tablename__ = "tool_grants"
    __table_args__ = (
        CheckConstraint("status IN ('active','revoked')", name="ck_tool_grant_status"),
        Index("ix_tool_grants_lookup", "tenant_id", "tool_name", "status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    config: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict
    )  # e.g. {"allowed_domains": [...]} for web.fetch
    granted_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ToolCall(Base):
    """Append-only log of every tool call. Inputs/outputs are stored redacted."""

    __tablename__ = "tool_calls"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key"),
        CheckConstraint("status IN ('succeeded','failed')", name="ck_tool_call_status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workflow_runs.id"), nullable=False, index=True
    )
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    input: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                        nullable=False, default=dict)
    output: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _created()


# --- Phase 11: artifacts and visual browser sessions ---------------------------------------


class Artifact(Base):
    """A stored output (screenshot, file, text). Bytes live in the artifact store; this row
    is the canonical record (content hash, type, size, provenance)."""

    __tablename__ = "artifacts"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"), index=True)
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    meta: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                       nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()


class BrowserSession(Base):
    """A sandbox browser the owner can watch and control.

    `status` is reported by the worker; `control` is the owner's command
    (none/pause/takeover/terminate). The worker DB role can't write `control`.
    """

    __tablename__ = "browser_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('active','paused','taken_over','terminated','closed')",
                        name="ck_browser_status"),
        CheckConstraint("control IN ('none','pause','takeover','terminate')",
                        name="ck_browser_control"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workflow_runs.id"), nullable=False)
    task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tasks.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    control: Mapped[str] = mapped_column(String(16), nullable=False, default="none")
    allowed_domains: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list)
    current_url: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    last_action: Mapped[str | None] = mapped_column(Text)
    last_screenshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("artifacts.id"))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# --- Phase 12: agent registry and factory ---------------------------------------------------

AGENT_STATUSES = ("draft", "active", "retired")


class AgentSpec(Base):
    """Spec vol. 05 AgentSpec: a versioned configuration, not an identity.

    Agents (the worker role) may only INSERT drafts (an RLS check enforces status='draft').
    Validation, evaluation, activation, retirement and rollback happen through the API.
    """

    __tablename__ = "agent_specs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version"),
        CheckConstraint("status IN ('draft','active','retired')", name="ck_agent_status"),
        CheckConstraint("budget_cents_per_run >= 0", name="ck_agent_budget"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(200), nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    instructions: Mapped[str] = mapped_column(Text, nullable=False)  # the versioned prompt
    model_profile: Mapped[str] = mapped_column(String(32), nullable=False, default="standard")
    allowed_tools: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list)
    budget_cents_per_run: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    max_runtime_s: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    success_tests: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list)
    output_checks: Mapped[dict] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=dict)
    escalation: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)  # owner|factory|import
    source_ref: Mapped[str | None] = mapped_column(String(300))
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("agent_specs.id"))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# --- Phase 13: opportunities ------------------------------------------------------------------

OPPORTUNITY_STATUSES = ("new", "researching", "validated", "rejected", "pursuing")


class Opportunity(Base):
    """A scored business opportunity with its evidence (spec vol. 03 "Opportunity engine").

    `signals` are normalized, timestamped, source-linked observations; `scores` holds the
    transparent component scores; `brief` is generated from them. The worker can update the
    evidence columns but never `status` (that's the owner's call).
    """

    __tablename__ = "opportunities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key"),
        CheckConstraint("status IN ('new','researching','validated','rejected','pursuing')",
                        name="ck_opportunity_status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(300), nullable=False)
    category: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str | None] = mapped_column(Text)
    signals: Mapped[list] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                          nullable=False, default=list)
    scores: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                         nullable=False, default=dict)
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    uncertainty: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    brief: Mapped[str] = mapped_column(Text, nullable=False, default="")
    sources: Mapped[list] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                          nullable=False, default=list)
    economics: Mapped[dict | None] = mapped_column(JSON().with_variant(JSONB(), "postgresql"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    created_by: Mapped[str] = mapped_column(String(16), nullable=False)  # owner|run
    source_ref: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# --- Phase 15: Shopify draft products -------------------------------------------------------


class ShopProduct(Base):
    """Canonical record of every product Jackson created on a store (mock or real)."""

    __tablename__ = "shop_products"
    __table_args__ = (
        UniqueConstraint("tenant_id", "backend", "handle"),
        CheckConstraint("backend IN ('mock','shopify')", name="ck_shop_backend"),
        CheckConstraint("status IN ('DRAFT','ACTIVE','ARCHIVED','UNLISTED')",
                        name="ck_shop_status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("opportunities.id"))
    backend: Mapped[str] = mapped_column(String(16), nullable=False)
    store: Mapped[str | None] = mapped_column(String(255))
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    handle: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="DRAFT")
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_url: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                          nullable=False, default=dict)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


# --- Phase 16: personas and content ----------------------------------------------------------

CHANNELS = ("instagram", "tiktok", "youtube_shorts", "x", "facebook", "email", "blog")
CONTENT_STATUSES = ("draft", "in_review", "approved", "rejected", "scheduled", "published",
                    "failed")


class Persona(Base):
    """A brand voice or an ORIGINAL FICTIONAL virtual influencer (spec vol. 03)."""

    __tablename__ = "personas"
    __table_args__ = (
        CheckConstraint("kind IN ('virtual_influencer','brand_voice')", name="ck_persona_kind"),
        CheckConstraint("kind <> 'virtual_influencer' OR fictional", name="ck_persona_fictional"),
        CheckConstraint("length(disclosure) > 0", name="ck_persona_disclosure"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    fictional: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bio: Mapped[str] = mapped_column(Text, nullable=False, default="")
    brand_bible: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                              nullable=False, default=dict)
    disclosure: Mapped[str] = mapped_column(String(300), nullable=False)
    do_not_impersonate: Mapped[list] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ContentItem(Base):
    """A piece of content. It can't be scheduled or published unless a person approved it
    (CHECK constraint), and the worker role can't write `approved_by`."""

    __tablename__ = "content_items"
    __table_args__ = (
        CheckConstraint(f"status IN {CONTENT_STATUSES}", name="ck_content_status"),
        CheckConstraint(f"channel IN {CHANNELS}", name="ck_content_channel"),
        # "Needs approval" is enforced by a trigger (ck_content_needs_approval): a person's
        # approval, or an active Launch approval that covers this channel (phase 21).
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    persona_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("personas.id"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    launch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("launches.id"))
    channel: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    hashtags: Mapped[list] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                           nullable=False, default=list)
    sponsored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assets: Mapped[list] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                         nullable=False, default=list)  # [{ref, rights, proof}]
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    review: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                         nullable=False, default=dict)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    external_post_id: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


SITE_STATUSES = ("draft", "ready", "live")


class SiteProject(Base):
    """A website for a business (spec vol. 03 website factory). `live_version_id` can only
    point at a version of this site that passed its tests (DB trigger)."""

    __tablename__ = "sites"
    __table_args__ = (CheckConstraint(f"status IN {SITE_STATUSES}", name="ck_site_status"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    requirements: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                               nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    domain: Mapped[str | None] = mapped_column(String(253))
    domain_token: Mapped[str | None] = mapped_column(String(64))
    live_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("site_versions.id", use_alter=True, name="fk_sites_live_version"))
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("site_versions.id", use_alter=True, name="fk_sites_previous_version"))
    live_url: Mapped[str | None] = mapped_column(String(500))
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class SiteVersion(Base):
    """One immutable build of a site: its files and its test results."""

    __tablename__ = "site_versions"
    __table_args__ = (UniqueConstraint("site_id", "number"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    site_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sites.id"), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    files: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                        nullable=False)  # {"index.html": "<!doctype ..."}
    checks: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                         nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = _created()


LEAD_SOURCES = ("inbound", "referral", "existing_customer", "event", "public_business_listing")
LAWFUL_BASES = ("consent", "legitimate_interest", "existing_relationship")
LEAD_STAGES = ("new", "contacted", "replied", "meeting", "proposal", "won", "lost")
OUTREACH_CHANNELS = ("email", "sms", "call")


class Contact(Base):
    """A lead or customer. Every contact records where it came from and the lawful basis
    for contacting it (spec vol. 03: lawful leads, permitted sources)."""

    __tablename__ = "contacts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key"),
        CheckConstraint(f"source_type IN {LEAD_SOURCES}", name="ck_contact_source"),
        CheckConstraint(f"lawful_basis IN {LAWFUL_BASES}", name="ck_contact_basis"),
        CheckConstraint(f"stage IN {LEAD_STAGES}", name="ck_contact_stage"),
        CheckConstraint("length(source_ref) > 0", name="ck_contact_source_ref"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    company: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    email: Mapped[str | None] = mapped_column(String(320))
    phone: Mapped[str | None] = mapped_column(String(32))
    region: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    industry: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    lawful_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    sms_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    call_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage: Mapped[str] = mapped_column(String(16), nullable=False, default="new")
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score_detail: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                               nullable=False, default=dict)
    dedupe_key: Mapped[str] = mapped_column(String(400), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Suppression(Base):
    """A permanent opt-out. Stored as a hash so it survives deleting the contact without
    keeping their address. Nobody (API or worker) can delete one."""

    __tablename__ = "suppressions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", "value_hash"),
        CheckConstraint("channel IN ('email','sms','call','all')", name="ck_suppression_channel"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    value_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = _created()


class OutreachCampaign(Base):
    __tablename__ = "outreach_campaigns"
    __table_args__ = (
        CheckConstraint(f"channel IN {OUTREACH_CHANNELS}", name="ck_campaign_channel"),
        CheckConstraint("status IN ('draft','prepared','approved','sending','completed',"
                        "'cancelled')", name="ck_campaign_status"),
        # ck_campaign_needs_approval is a trigger: a person, or a covering Launch approval.
        CheckConstraint("daily_limit BETWEEN 1 AND 500", name="ck_campaign_daily_limit"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    launch_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("launches.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    sender_name: Mapped[str] = mapped_column(String(200), nullable=False)
    sender_email: Mapped[str] = mapped_column(String(320), nullable=False, default="")
    postal_address: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    subject_template: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    body_template: Mapped[str] = mapped_column(Text, nullable=False)
    audience: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                           nullable=False, default=dict)
    daily_limit: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class OutreachMessage(Base):
    """One message to one contact. A DB trigger refuses to mark it sent if the address is
    suppressed, the campaign isn't approved, or an SMS/call has no recorded consent."""

    __tablename__ = "outreach_messages"
    __table_args__ = (
        UniqueConstraint("campaign_id", "contact_id"),
        CheckConstraint("status IN ('blocked','queued','sending','sent','failed','skipped')",
                        name="ck_message_status"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    campaign_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("outreach_campaigns.id"),
                                                   nullable=False)
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("contacts.id", ondelete="SET NULL"))
    channel: Mapped[str] = mapped_column(String(8), nullable=False)
    to_address: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    review: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                         nullable=False, default=dict)
    external_id: Mapped[str | None] = mapped_column(String(255))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class RevenueEntry(Base):
    """Revenue, kept apart by kind so the dashboard never mixes estimates with actuals
    (spec vol. 04). Append-only: corrections are new entries."""

    __tablename__ = "revenue_entries"
    __table_args__ = (
        CheckConstraint("kind IN ('estimate','actual','refund')", name="ck_revenue_kind"),
        CheckConstraint("amount_cents >= 0", name="ck_revenue_amount"),
        CheckConstraint("source IN ('manual','shopify','whop','stripe','import')",
                        name="ck_revenue_source"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("businesses.id"))
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    period: Mapped[str] = mapped_column(String(7), nullable=False)  # YYYY-MM
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    reference: Mapped[str] = mapped_column(String(300), nullable=False, default="")
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = _created()


LAUNCH_STATUSES = ("proposed", "active", "paused", "revoked", "completed", "killed", "expired",
                   "rejected")


class Launch(Base):
    """A standing approval (phase 21, ADR-032): the owner approves a launch ONCE with a
    budget, a daily cap, a time window, the channels allowed and kill rules. Inside that
    scope Jackson acts without per-action approvals; anything outside it still goes to
    the approvals inbox. Only the owner can approve, widen, resume or revoke it."""

    __tablename__ = "launches"
    __table_args__ = (
        CheckConstraint(f"status IN {LAUNCH_STATUSES}", name="ck_launch_status"),
        CheckConstraint("status IN ('proposed','rejected') OR (approved_by IS NOT NULL "
                        "AND starts_at IS NOT NULL AND ends_at > starts_at)",
                        name="ck_launch_approved"),
        CheckConstraint("total_budget_cents >= 0 AND daily_cap_cents >= 0 "
                        "AND daily_cap_cents <= total_budget_cents", name="ck_launch_budget"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    opportunity_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("opportunities.id"))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    plan: Mapped[str] = mapped_column(Text, nullable=False, default="")
    proposed_by: Mapped[str] = mapped_column(String(16), nullable=False, default="owner")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="proposed")
    status_reason: Mapped[str | None] = mapped_column(Text)
    total_budget_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    daily_cap_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    channels: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                           nullable=False, default=dict)
    kill_rules: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                             nullable=False, default=dict)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class LaunchSignal(Base):
    """Outcome signals for a launch's kill rules and anomaly checks: sales, refunds,
    sends, bounces, complaints, platform errors. Append-only."""

    __tablename__ = "launch_signals"
    __table_args__ = (
        CheckConstraint("kind IN ('sale','refund','send','bounce','complaint',"
                        "'platform_error')", name="ck_launch_signal_kind"),
        CheckConstraint("quantity >= 0 AND amount_cents >= 0", name="ck_launch_signal_values"),
        UniqueConstraint("launch_id", "kind", "external_ref"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    launch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("launches.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = _created()


# --- Phase 23: the City command center (ADR-037) -------------------------------------

AGENT_STATUSES = ("idle", "working", "done", "waiting", "failed", "offline", "paused",
                  "killed")


class Department(Base):
    __tablename__ = "departments"
    __table_args__ = (UniqueConstraint("tenant_id", "key"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[str] = mapped_column(String(16), nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created()


class Floor(Base):
    __tablename__ = "floors"
    __table_args__ = (UniqueConstraint("tenant_id", "department_id", "key"),)
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    department_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("departments.id"),
                                                     nullable=False)
    key: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = _created()


class Agent(Base):
    """A real worker that exists in code (a task handler) or an AgentSpec. Its status is
    only ever set by the worker (from real task transitions) or the owner (pause/kill)."""

    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "key"),
        CheckConstraint(f"status IN {AGENT_STATUSES}", name="ck_agent_status_city"),
    )
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[str] = mapped_column(String(200), nullable=False)
    task_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    department_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("departments.id"))
    floor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("floors.id"))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="idle")
    model: Mapped[str | None] = mapped_column(String(100))
    current_task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now())
    daily_limit_usd: Mapped[float | None] = mapped_column(Numeric(10, 2))
    created_at: Mapped[datetime] = _created()


class CityShare(Base):
    """A read-only link to one client's (business's) view of the City."""

    __tablename__ = "city_shares"
    id: Mapped[uuid.UUID] = _pk()
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tenants.id"), nullable=False, index=True
    )
    business_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("businesses.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()


class Event(Base):
    """Append-only: everything the City shows comes from these rows."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_tenant_id_id", "tenant_id", "id"),
        Index("ix_events_ts", "ts"),
        Index("ix_events_agent_ts", "agent_id", "ts"),
        Index("ix_events_task", "task_id"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id"), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                         nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_kind: Mapped[str | None] = mapped_column(String(16))
    from_id: Mapped[str | None] = mapped_column(String(100))
    to_kind: Mapped[str | None] = mapped_column(String(16))
    to_id: Mapped[str | None] = mapped_column(String(100))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("workflow_runs.id"))
    task_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tasks.id"))
    agent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("agents.id"))
    payload: Mapped[dict] = mapped_column(JSON().with_variant(JSONB(), "postgresql"),
                                          nullable=False, default=dict)
