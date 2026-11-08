"""Anthropic provider tests.

Request-building and parsing are tested directly. The end-to-end SDK path uses the REAL
`anthropic` SDK with an httpx2 MockTransport (MOCK HTTP: no network, fake responses), which
verifies the exact wire payload we send. Live API behaviour is covered in tests/live.
"""

import json

import anthropic
import httpx2
import pytest

from jaxon.core.errors import ConfigurationError, ProviderError
from jaxon.providers.anthropic_provider import (
    FALLBACK_BETA,
    AnthropicProvider,
    build_request_kwargs,
)
from jaxon.providers.base import (
    ChatMessage,
    CompletionRequest,
    StopReason,
    ToolCall,
    ToolResult,
    ToolSpec,
)


def _req(**kw) -> CompletionRequest:
    base = dict(
        model="claude-opus-5-5",
        system="sys",
        messages=[ChatMessage(role="user", text="hi")],
        max_tokens=100,
        effort="high",
        tools=[
            ToolSpec(
                name="math__calculate",
                description="d",
                input_schema={"type": "object", "properties": {}},
            )
        ],
    )
    base.update(kw)
    return CompletionRequest(**base)


def test_build_kwargs_with_fallback():
    k = build_request_kwargs(_req(), use_fallback=True)
    assert k["betas"] == [FALLBACK_BETA] and k["fallbacks"] == "default"
    assert k["output_config"] == {"effort": "high"} and k["system"] == "sys"
    assert k["tools"][0]["name"] == "math__calculate"
    assert "thinking" not in k and "tool_choice" not in k


def test_build_kwargs_no_fallback_for_unsupported_model():
    k = build_request_kwargs(_req(model="claude-haiku-4-5", effort=None), use_fallback=True)
    assert "betas" not in k and "fallbacks" not in k and "output_config" not in k


def test_wire_messages_replay_and_tool_results():
    native = [
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "tool_use", "id": "t1", "name": "math__calculate", "input": {"e": 1}},
    ]
    msgs = [
        ChatMessage(role="user", text="q"),
        ChatMessage(
            role="assistant",
            provider="anthropic",
            provider_content=native,
            tool_calls=[ToolCall(id="t1", name="math__calculate")],
        ),
        ChatMessage(role="user", tool_results=[ToolResult(tool_call_id="t1", content="{}")]),
        ChatMessage(
            role="assistant",
            provider="other",
            text="from another provider",
            provider_content=[{"type": "weird"}],
        ),
    ]
    wire = build_request_kwargs(_req(messages=msgs), use_fallback=False)["messages"]
    assert wire[1]["content"] == native  # replayed byte-for-byte
    assert wire[2]["content"][0]["type"] == "tool_result"
    assert wire[3]["content"] == [{"type": "text", "text": "from another provider"}]


def _client(handler) -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(
        api_key="test-key-not-real",
        max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def _message(content, stop="end_turn", **extra):
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 7},
        **extra,
    }


async def test_sdk_roundtrip_tool_use(settings):
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["url"] = str(request.url)
        seen["beta"] = request.headers.get("anthropic-beta")
        seen["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json=_message(
                [
                    {"type": "text", "text": "Calculating"},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "math__calculate",
                        "input": {"expression": "2+2"},
                    },
                ],
                stop="tool_use",
            ),
        )

    p = AnthropicProvider(settings, client=_client(handler))
    resp = await p.complete(_req())
    assert "/v1/messages" in seen["url"] and FALLBACK_BETA in (seen["beta"] or "")
    assert seen["body"]["fallbacks"] == "default"
    assert seen["body"]["output_config"] == {"effort": "high"}
    assert resp.stop_reason == StopReason.tool_use
    assert resp.message.text == "Calculating"
    assert resp.message.tool_calls[0].arguments == {"expression": "2+2"}
    assert resp.message.provider_content[1]["type"] == "tool_use"
    assert resp.usage.input_tokens == 12 and resp.model == "claude-opus-5-5"


async def test_sdk_refusal(settings):
    settings.anthropic_refusal_fallback = False
    p = AnthropicProvider(
        settings,
        client=_client(
            lambda r: httpx2.Response(
                200,
                json=_message(
                    [],
                    stop="refusal",
                    stop_details={"type": "refusal", "category": "cyber", "explanation": "no"},
                ),
            )
        ),
    )
    resp = await p.complete(_req())
    assert resp.stop_reason == StopReason.refusal and resp.refusal["category"] == "cyber"


@pytest.mark.parametrize(
    "status,exc", [(401, ConfigurationError), (429, ProviderError), (500, ProviderError)]
)
async def test_sdk_errors_are_mapped(settings, status, exc):
    body = {"type": "error", "error": {"type": "x", "message": "boom"}}
    p = AnthropicProvider(settings, client=_client(lambda r: httpx2.Response(status, json=body)))
    with pytest.raises(exc):
        await p.complete(_req())


async def test_missing_key_is_configuration_error(settings, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    p = AnthropicProvider(settings)
    assert not p.is_configured()
    with pytest.raises(ConfigurationError):
        await p.complete(_req())
