"""Built-in specialist agents. Custom agents are stored in the agent_definitions table."""

from __future__ import annotations

from jaxon.agents.base import AgentSpec

_BASE = (
    "You are part of JAXON OS, an AI business operating system. Be accurate and concise. "
    "Use the available tools when they help; never invent tool results, facts, contacts or "
    "numbers. If a tool fails, say so plainly. Only access websites you were authorized to."
)

BUILTIN_AGENTS: dict[str, AgentSpec] = {
    a.name: a
    for a in [
        AgentSpec(
            name="general",
            description="General-purpose operator with core, math and memory skills.",
            system_prompt=_BASE,
            skills=["core.current_time", "math.calculate", "memory.remember", "memory.recall"],
            builtin=True,
        ),
        AgentSpec(
            name="researcher",
            description="Researches authorized web sources and stores findings in memory.",
            system_prompt=_BASE + " You research topics from primary sources, cite the URLs "
            "you used, and save key findings to memory.",
            skills=[
                "web.fetch_url",
                "browser.page_text",
                "text.extract_contacts",
                "memory.remember",
                "memory.recall",
            ],
            builtin=True,
        ),
        AgentSpec(
            name="analyst",
            description="Business/financial analysis with exact arithmetic.",
            system_prompt=_BASE + " You analyse business opportunities and numbers. Always use "
            "math.calculate for arithmetic and show the key figures.",
            skills=["math.calculate", "memory.recall"],
            builtin=True,
        ),
    ]
}
