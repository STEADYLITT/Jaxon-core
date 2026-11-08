from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorBody(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class InvokeSkillRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    approved: bool = False


class CreateRunRequest(BaseModel):
    agent: str = Field(min_length=1, max_length=100)
    input: str = Field(min_length=1, max_length=100_000)
    mode: Literal["queue", "sync"] = "queue"


class RunStepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    index: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    agent_name: str
    status: str
    input: str
    output: str | None
    error: dict[str, Any] | None
    model: str | None
    stop_reason: str | None
    usage: dict[str, Any]
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class RunDetailOut(RunOut):
    steps: list[RunStepOut]


class RunListOut(BaseModel):
    items: list[RunOut]
    total: int


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)
    namespace: str = Field(default="default", max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemorySearch(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)
    namespace: str = Field(default="default", max_length=100)
    limit: int = Field(default=5, ge=1, le=50)
