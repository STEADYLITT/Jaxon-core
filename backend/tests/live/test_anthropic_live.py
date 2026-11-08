"""LIVE tests against the real Anthropic API. Skipped unless ANTHROPIC_API_KEY is set.

Run explicitly:  ANTHROPIC_API_KEY=... pytest tests/live -m live
"""

import os

import pytest

from jaxon.agents.base import AgentSpec
from jaxon.agents.executor import AgentExecutor
from jaxon.providers.anthropic_provider import AnthropicProvider
from jaxon.providers.base import ChatMessage, CompletionRequest, StopReason

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY not set"),
]


@pytest.fixture
def live_settings(settings):
    from pydantic import SecretStr

    settings.anthropic_api_key = SecretStr(os.environ["ANTHROPIC_API_KEY"])
    settings.model_effort = "low"
    return settings


async def test_simple_completion(live_settings):
    p = AnthropicProvider(live_settings)
    resp = await p.complete(
        CompletionRequest(
            model=live_settings.default_model,
            max_tokens=2000,
            effort="low",
            messages=[ChatMessage(role="user", text="Reply with exactly: JAXON ONLINE")],
        )
    )
    await p.aclose()
    assert resp.stop_reason == StopReason.end_turn
    assert "JAXON ONLINE" in (resp.message.text or "")


async def test_agent_uses_calculator(live_settings, registry, ctx):
    p = AnthropicProvider(live_settings)
    ex = AgentExecutor(
        p,
        registry,
        default_model=live_settings.default_model,
        default_effort="low",
        max_tokens=4000,
    )
    spec = AgentSpec(
        name="live-analyst",
        system_prompt="Always use math__calculate for math.",
        skills=["math.calculate"],
        max_steps=4,
    )
    res = await ex.run(spec, "What is 48271 * 913? Use the tool.", ctx)
    await p.aclose()
    assert res.status == "succeeded", res.error
    assert any(s.kind == "skill_call" for s in res.steps)
    assert "44071423" in (res.output or "").replace(",", "")
