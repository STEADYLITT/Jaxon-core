"""Provider-independent agent loop: model -> skill calls -> model ... until done."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic import BaseModel, Field

from jaxon.agents.base import AgentSpec
from jaxon.core.errors import JaxonError, ValidationFailed
from jaxon.core.logging import get_logger
from jaxon.providers.base import (
    ChatMessage,
    CompletionRequest,
    ModelProvider,
    StopReason,
    ToolResult,
    ToolSpec,
    Usage,
)
from jaxon.skills.base import SkillContext
from jaxon.skills.registry import SkillRegistry

log = get_logger(__name__)

MAX_TOOL_RESULT_CHARS = 50_000


def tool_name(skill_name: str) -> str:
    """Provider-safe tool name (most APIs reject dots)."""
    return skill_name.replace(".", "__")


def skill_name(tool: str) -> str:
    return tool.replace("__", ".")


class StepEvent(BaseModel):
    index: int
    kind: str  # model_response | skill_call | skill_result
    payload: dict[str, Any]


class AgentResult(BaseModel):
    status: str  # succeeded | failed
    output: str | None
    stop_reason: str
    model: str
    usage: Usage
    steps: list[StepEvent] = Field(default_factory=list)
    error: dict[str, Any] | None = None


StepCallback = Callable[[StepEvent], Awaitable[None]]


class AgentExecutor:
    def __init__(
        self,
        provider: ModelProvider,
        skills: SkillRegistry,
        *,
        default_model: str,
        default_effort: str | None,
        max_tokens: int,
    ) -> None:
        self.provider = provider
        self.skills = skills
        self.default_model = default_model
        self.default_effort = default_effort
        self.max_tokens = max_tokens

    def tools_for(self, spec: AgentSpec) -> list[ToolSpec]:
        missing = [s for s in spec.skills if s not in self.skills]
        if missing:
            raise ValidationFailed(
                f"Agent '{spec.name}' references unknown skills", details={"missing": missing}
            )
        tools = []
        for name in spec.skills:
            m = type(self.skills.get(name)).manifest()
            tools.append(
                ToolSpec(
                    name=tool_name(name), description=m.description, input_schema=m.input_schema
                )
            )
        return tools

    async def run(
        self,
        spec: AgentSpec,
        user_input: str,
        ctx: SkillContext,
        on_step: StepCallback | None = None,
    ) -> AgentResult:
        tools = self.tools_for(spec)
        model = spec.model or self.default_model
        messages: list[ChatMessage] = [ChatMessage(role="user", text=user_input)]
        steps: list[StepEvent] = []
        usage = Usage()

        async def emit(kind: str, payload: dict[str, Any]) -> None:
            ev = StepEvent(index=len(steps), kind=kind, payload=payload)
            steps.append(ev)
            if on_step:
                await on_step(ev)

        def finish(
            status: str, output: str | None, stop: str, error: dict[str, Any] | None = None
        ) -> AgentResult:
            log.info(
                "agent.finished",
                agent=spec.name,
                status=status,
                stop_reason=stop,
                steps=len(steps),
                output_tokens=usage.output_tokens,
            )
            return AgentResult(
                status=status,
                output=output,
                stop_reason=stop,
                model=model,
                usage=usage,
                steps=steps,
                error=error,
            )

        for turn in range(spec.max_steps):
            req = CompletionRequest(
                model=model,
                system=spec.system_prompt,
                messages=messages,
                tools=tools,
                max_tokens=self.max_tokens,
                effort=spec.effort or self.default_effort,
                metadata={"agent": spec.name, "turn": turn},
            )
            try:
                resp = await self.provider.complete(req)
            except JaxonError as e:
                return finish("failed", None, "error", e.to_dict())
            usage = usage + resp.usage
            model = resp.model or model
            messages.append(resp.message)
            await emit(
                "model_response",
                {
                    "text": resp.message.text,
                    "stop_reason": resp.raw_stop_reason,
                    "tool_calls": [c.model_dump() for c in resp.message.tool_calls],
                    "usage": resp.usage.model_dump(),
                    "model": resp.model,
                },
            )

            if resp.stop_reason == StopReason.refusal:
                return finish(
                    "failed",
                    resp.message.text,
                    "refusal",
                    {
                        "code": "model_refusal",
                        "message": "The model declined the request",
                        "details": resp.refusal or {},
                    },
                )
            if resp.stop_reason == StopReason.pause_turn:
                continue  # server paused a long turn; resend to let it resume
            if resp.stop_reason != StopReason.tool_use or not resp.message.tool_calls:
                status = "succeeded" if resp.stop_reason == StopReason.end_turn else "failed"
                err = (
                    None
                    if status == "succeeded"
                    else {"code": "incomplete", "message": f"Stopped: {resp.raw_stop_reason}"}
                )
                return finish(status, resp.message.text, resp.stop_reason.value, err)

            results: list[ToolResult] = []
            for call in resp.message.tool_calls:
                sname = skill_name(call.name)
                await emit(
                    "skill_call",
                    {"tool_call_id": call.id, "skill": sname, "arguments": call.arguments},
                )
                content: dict[str, Any] | None
                if sname not in spec.skills:
                    res_ok, duration = False, 0.0
                    content = {
                        "code": "not_allowed",
                        "message": f"Skill '{sname}' not enabled for agent",
                    }
                else:
                    r = await self.skills.invoke(sname, call.arguments, ctx)
                    res_ok, content, duration = r.ok, (r.output if r.ok else r.error), r.duration_ms
                text = json.dumps(content, default=str)
                if len(text) > MAX_TOOL_RESULT_CHARS:
                    text = text[:MAX_TOOL_RESULT_CHARS] + "...[truncated]"
                results.append(ToolResult(tool_call_id=call.id, content=text, is_error=not res_ok))
                await emit(
                    "skill_result",
                    {
                        "tool_call_id": call.id,
                        "skill": sname,
                        "ok": res_ok,
                        "result": content,
                        "duration_ms": duration,
                    },
                )
            # All results go back in a single user message.
            messages.append(ChatMessage(role="user", tool_results=results))

        return finish(
            "failed",
            None,
            "max_steps",
            {"code": "max_steps", "message": f"Exceeded {spec.max_steps} steps"},
        )
