"""Persistent data model. Keep in sync with migrations/versions."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from jaxon.db.base import Base, TimestampMixin, utcnow

# Must match the vector(...) size in migration 0001 and Settings.embedding_dim.
EMBEDDING_DIM = 384


class RunStatus(enum.StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


class AgentDefinition(TimestampMixin, Base):
    """User-defined agent specs. Built-in specialists live in code (jaxon.agents.builtin)."""

    __tablename__ = "agent_definitions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(100))
    effort: Mapped[str | None] = mapped_column(String(20))
    skills: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    max_steps: Mapped[int] = mapped_column(Integer, nullable=False, default=12)


class AgentRun(TimestampMixin, Base):
    __tablename__ = "agent_runs"
    __table_args__ = (Index("ix_agent_runs_status_created", "status", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    agent_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", values_callable=lambda e: [m.value for m in e]),
        nullable=False,
        default=RunStatus.queued,
    )
    input: Mapped[str] = mapped_column(Text, nullable=False)
    output: Mapped[str | None] = mapped_column(Text)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model: Mapped[str | None] = mapped_column(String(100))
    stop_reason: Mapped[str | None] = mapped_column(String(50))
    usage: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    steps: Mapped[list[RunStep]] = relationship(
        back_populates="run",
        order_by="RunStep.index",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class RunStep(Base):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "index", name="uq_run_steps_run_id_index"),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    # model_response | skill_call | skill_result
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, server_default=func.now()
    )

    run: Mapped[AgentRun] = relationship(back_populates="steps")


class Memory(TimestampMixin, Base):
    """Long-term memory item with a pgvector embedding."""

    __tablename__ = "memories"
    __table_args__ = (
        Index(
            "ix_memories_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    namespace: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True, default="default"
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    meta: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
