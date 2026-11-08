"""Provider-independent model interface.

Agents and skills only ever talk to `ModelProvider`. Each provider converts these neutral
types to its own wire format. Provider-specific content (e.g. Claude thinking blocks) is kept
opaque in `ChatMessage.provider_content` and replayed unchanged to the same provider, so
multi-turn tool loops stay valid without the core knowing provider internals.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolSpec(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResult(BaseModel):
    tool_call_id: str
    content: str
    is_error: bool = False


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    text: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    # Opaque provider-native content, replayed verbatim to the provider named here.
    provider: str | None = None
    provider_content: list[dict[str, Any]] | None = None


class StopReason(enum.StrEnum):
    end_turn = "end_turn"
    tool_use = "tool_use"
    max_tokens = "max_tokens"
    refusal = "refusal"
    pause_turn = "pause_turn"
    other = "other"


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
        )


class CompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    system: str | None = None
    tools: list[ToolSpec] = Field(default_factory=list)
    max_tokens: int = 16000
    effort: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CompletionResponse(BaseModel):
    message: ChatMessage
    stop_reason: StopReason
    raw_stop_reason: str | None = None
    model: str
    usage: Usage = Field(default_factory=Usage)
    refusal: dict[str, Any] | None = None
    request_id: str | None = None


class ModelProvider(ABC):
    name: str

    @abstractmethod
    async def complete(self, request: CompletionRequest) -> CompletionResponse: ...

    def is_configured(self) -> bool:
        """True if credentials are present. Does NOT prove the provider works."""
        return True

    async def aclose(self) -> None:  # noqa: B027 - optional hook
        pass
