"""Agent loop tests driven by the ScriptedProvider MOCK fixture (no real model calls)."""

import json

import pytest

from jaxon.agents.base import AgentSpec
from jaxon.agents.executor import AgentExecutor, skill_name, tool_name
from jaxon.core.errors import ProviderError, ValidationFailed
from jaxon.providers.base import StopReason
from tests.fixtures import ScriptedProvider, call, reply

SPEC = AgentSpec(
    name="tester", system_prompt="sys", skills=["math.calculate", "core.echo"], max_steps=4
)


def make(provider, registry) -> AgentExecutor:
    return AgentExecutor(
        provider, registry, default_model="claude-opus-5-5", default_effort="high", max_tokens=1000
    )


def test_tool_name_roundtrip():
    assert tool_name("math.calculate") == "math__calculate"
    assert skill_name("math__calculate") == "math.calculate"


async def test_direct_answer(registry, ctx):
    p = ScriptedProvider([reply("hello")])
    res = await make(p, registry).run(SPEC, "hi", ctx)
    assert res.status == "succeeded" and res.output == "hello"
    req = p.requests[0]
    assert req.system == "sys" and req.effort == "high" and req.model == "claude-opus-5-5"
    assert {t.name for t in req.tools} == {"math__calculate", "core__echo"}


async def test_tool_loop(registry, ctx):
    def final(req):
        last = req.messages[-1]
        assert last.role == "user" and len(last.tool_results) == 1
        payload = json.loads(last.tool_results[0].content)
        assert payload["result"] == 184.0 and not last.tool_results[0].is_error
        return reply("The answer is 184")

    p = ScriptedProvider([call("math__calculate", {"expression": "1200*0.15+4"}), final])
    events = []

    async def on_step(ev):
        events.append(ev.kind)

    res = await make(p, registry).run(SPEC, "compute", ctx, on_step=on_step)
    assert res.status == "succeeded" and res.output == "The answer is 184"
    assert events == ["model_response", "skill_call", "skill_result", "model_response"]
    assert res.usage.input_tokens == 20


async def test_skill_not_enabled_is_reported_to_model(registry, ctx):
    def final(req):
        r = req.messages[-1].tool_results[0]
        assert r.is_error and "not_allowed" in r.content
        return reply("ok")

    p = ScriptedProvider([call("web__fetch_url", {"url": "https://example.com"}), final])
    res = await make(p, registry).run(SPEC, "x", ctx)
    assert res.status == "succeeded"


async def test_bad_tool_args_are_returned_as_error(registry, ctx):
    def final(req):
        r = req.messages[-1].tool_results[0]
        assert r.is_error and "validation_failed" in r.content
        return reply("fixed")

    p = ScriptedProvider([call("math__calculate", {"wrong": 1}), final])
    assert (await make(p, registry).run(SPEC, "x", ctx)).status == "succeeded"


async def test_refusal(registry, ctx):
    p = ScriptedProvider([reply("no", StopReason.refusal)])
    res = await make(p, registry).run(SPEC, "x", ctx)
    assert res.status == "failed" and res.error["code"] == "model_refusal"


async def test_max_tokens_is_failure(registry, ctx):
    p = ScriptedProvider([reply("partial", StopReason.max_tokens)])
    res = await make(p, registry).run(SPEC, "x", ctx)
    assert res.status == "failed" and res.stop_reason == "max_tokens"


async def test_max_steps(registry, ctx):
    p = ScriptedProvider([call("core__echo", {"text": "a"}, call_id=f"c{i}") for i in range(4)])
    res = await make(p, registry).run(SPEC, "loop", ctx)
    assert res.status == "failed" and res.error["code"] == "max_steps"


async def test_provider_error(registry, ctx):
    class Failing(ScriptedProvider):
        async def complete(self, request):
            raise ProviderError("down")

    res = await make(Failing([]), registry).run(SPEC, "x", ctx)
    assert res.status == "failed" and res.error["code"] == "provider_error"


async def test_unknown_skill_in_spec(registry, ctx):
    bad = SPEC.model_copy(update={"skills": ["does.not_exist"]})
    with pytest.raises(ValidationFailed):
        await make(ScriptedProvider([]), registry).run(bad, "x", ctx)
