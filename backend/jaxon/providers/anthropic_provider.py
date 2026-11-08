"""Anthropic Claude provider (Messages API via the official `anthropic` SDK)."""

from __future__ import annotations

import os
from typing import Any

import anthropic

from jaxon.core.config import Settings
from jaxon.core.errors import ConfigurationError, ProviderError
from jaxon.core.logging import get_logger
from jaxon.providers.base import (
    ChatMessage,
    CompletionRequest,
    CompletionResponse,
    ModelProvider,
    StopReason,
    ToolCall,
    Usage,
)

log = get_logger(__name__)

# Models that accept the server-side refusal fallback in "default" routing mode.
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_STOP_MAP = {
    "end_turn": StopReason.end_turn,
    "stop_sequence": StopReason.end_turn,
    "tool_use": StopReason.tool_use,
    "max_tokens": StopReason.max_tokens,
    "refusal": StopReason.refusal,
    "pause_turn": StopReason.pause_turn,
}


def _to_wire_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    wire: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "assistant" and m.provider == "anthropic" and m.provider_content:
            # Replay native blocks (thinking, tool_use, fallback...) exactly as received.
            wire.append({"role": "assistant", "content": m.provider_content})
            continue
        blocks: list[dict[str, Any]] = []
        # Tool results must come first in a user turn and all in one message.
        for r in m.tool_results:
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": r.tool_call_id,
                    "content": r.content,
                    "is_error": r.is_error,
                }
            )
        if m.text:
            blocks.append({"type": "text", "text": m.text})
        for c in m.tool_calls:
            blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
        if not blocks:
            raise ProviderError(f"Empty {m.role} message cannot be sent to Anthropic")
        wire.append({"role": m.role, "content": blocks})
    return wire


def build_request_kwargs(req: CompletionRequest, *, use_fallback: bool) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": req.model,
        "max_tokens": req.max_tokens,
        "messages": _to_wire_messages(req.messages),
    }
    if req.system:
        kwargs["system"] = req.system
    if req.tools:
        kwargs["tools"] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in req.tools
        ]
    if req.effort:
        kwargs["output_config"] = {"effort": req.effort}
    if use_fallback and req.model in FALLBACK_MODELS:
        kwargs["betas"] = [FALLBACK_BETA]
        kwargs["fallbacks"] = "default"
    return kwargs


def parse_response(resp: Any) -> CompletionResponse:
    texts: list[str] = []
    calls: list[ToolCall] = []
    raw_blocks: list[dict[str, Any]] = []
    for block in resp.content:
        raw_blocks.append(block.to_dict())
        btype = getattr(block, "type", None)
        if btype == "text":
            texts.append(block.text)
        elif btype == "tool_use":
            calls.append(ToolCall(id=block.id, name=block.name, arguments=dict(block.input or {})))
    raw_stop = getattr(resp, "stop_reason", None)
    refusal = None
    if raw_stop == "refusal":
        details = getattr(resp, "stop_details", None)
        refusal = details.to_dict() if details is not None else {}
    u = getattr(resp, "usage", None)
    usage = Usage(
        input_tokens=getattr(u, "input_tokens", 0) or 0,
        output_tokens=getattr(u, "output_tokens", 0) or 0,
        cache_read_input_tokens=getattr(u, "cache_read_input_tokens", 0) or 0,
        cache_creation_input_tokens=getattr(u, "cache_creation_input_tokens", 0) or 0,
    )
    return CompletionResponse(
        message=ChatMessage(
            role="assistant",
            text="\n".join(texts) if texts else None,
            tool_calls=calls,
            provider="anthropic",
            provider_content=raw_blocks,
        ),
        stop_reason=_STOP_MAP.get(raw_stop or "", StopReason.other),
        raw_stop_reason=raw_stop,
        model=getattr(resp, "model", ""),
        usage=usage,
        refusal=refusal,
        request_id=getattr(resp, "_request_id", None),
    )


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None) -> None:
        self._settings = settings
        self._client = client

    def is_configured(self) -> bool:
        return bool(
            self._client is not None
            or self._settings.anthropic_api_key
            or os.environ.get("ANTHROPIC_AUTH_TOKEN")
        )

    def _get_client(self) -> anthropic.AsyncAnthropic:
        if self._client is None:
            if not self.is_configured():
                raise ConfigurationError(
                    "ANTHROPIC_API_KEY is not set; the Anthropic provider cannot run.",
                    details={"missing": ["ANTHROPIC_API_KEY"]},
                )
            key = self._settings.anthropic_api_key
            self._client = anthropic.AsyncAnthropic(
                api_key=key.get_secret_value() if key else None,
                timeout=self._settings.model_timeout_seconds,
            )
        return self._client

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        client = self._get_client()
        kwargs = build_request_kwargs(
            request, use_fallback=self._settings.anthropic_refusal_fallback
        )
        try:
            if "betas" in kwargs:
                resp = await client.beta.messages.create(**kwargs)
            else:
                resp = await client.messages.create(**kwargs)
        except anthropic.AuthenticationError as e:
            raise ConfigurationError(
                "Anthropic rejected the API key", details={"status": 401}
            ) from e
        except anthropic.RateLimitError as e:
            raise ProviderError("Anthropic rate limit exceeded", details={"status": 429}) from e
        except anthropic.APIStatusError as e:
            raise ProviderError(
                f"Anthropic API error: {e.message}",
                details={"status": e.status_code, "retryable": e.status_code >= 500},
            ) from e
        except anthropic.APIConnectionError as e:
            raise ProviderError("Could not reach Anthropic API", details={"retryable": True}) from e
        parsed = parse_response(resp)
        log.info(
            "model.completion",
            provider=self.name,
            model=parsed.model,
            stop_reason=parsed.raw_stop_reason,
            input_tokens=parsed.usage.input_tokens,
            output_tokens=parsed.usage.output_tokens,
            request_id=parsed.request_id,
        )
        return parsed

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.close()
