"""TEST FIXTURES ONLY - mock/scripted components. Never imported by application code."""

from __future__ import annotations

from collections.abc import Callable

from jaxon.providers.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    ModelProvider,
    StopReason,
    ToolCall,
    Usage,
)

Script = Callable[[CompletionRequest], CompletionResponse]


def reply(text: str, stop: StopReason = StopReason.end_turn) -> CompletionResponse:
    return CompletionResponse(
        message=ChatMessage(role="assistant", text=text, provider="scripted"),
        stop_reason=stop,
        raw_stop_reason=stop.value,
        model="scripted-model",
        usage=Usage(input_tokens=10, output_tokens=5),
    )


def call(
    tool: str, args: dict, call_id: str = "call_1", text: str | None = None
) -> CompletionResponse:
    return CompletionResponse(
        message=ChatMessage(
            role="assistant",
            text=text,
            provider="scripted",
            tool_calls=[ToolCall(id=call_id, name=tool, arguments=args)],
        ),
        stop_reason=StopReason.tool_use,
        raw_stop_reason="tool_use",
        model="scripted-model",
        usage=Usage(input_tokens=10, output_tokens=5),
    )


class ScriptedProvider(ModelProvider):
    """MOCK provider: returns pre-scripted responses in order and records every request."""

    name = "scripted"

    def __init__(self, responses: list[CompletionResponse | Script]) -> None:
        self.responses = list(responses)
        self.requests: list[CompletionRequest] = []

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request.model_copy(deep=True))
        if not self.responses:
            raise AssertionError("ScriptedProvider ran out of responses")
        nxt = self.responses.pop(0)
        return nxt(request) if callable(nxt) else nxt
