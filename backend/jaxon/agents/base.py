from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

AGENT_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{1,99}$")


class AgentSpec(BaseModel):
    """Declarative definition of a specialist agent."""

    name: str
    description: str = ""
    system_prompt: str
    skills: list[str] = Field(default_factory=list)
    model: str | None = None  # None -> settings.default_model
    effort: Literal["low", "medium", "high", "xhigh", "max"] | None = None
    max_steps: int = Field(default=12, ge=1, le=100)
    builtin: bool = False

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not AGENT_NAME_RE.match(v):
            raise ValueError("name must be lowercase letters/digits/-/_ (2-100 chars)")
        return v
