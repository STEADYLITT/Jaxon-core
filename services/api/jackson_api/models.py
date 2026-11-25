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
