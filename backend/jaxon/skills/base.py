"""Skill interface.

A skill is one typed, self-describing capability (input model -> output model). Agents see
skills as tools; workflows and the API can call them directly. Two ways to write one:

    class Echo(Skill[EchoIn, EchoOut]):
        name = "core.echo"
        description = "Return the input text."
        Input = EchoIn
        Output = EchoOut
        async def run(self, inp, ctx): return EchoOut(text=inp.text)

    @skill(name="core.echo", description="Return the input text.")
    async def echo(inp: EchoIn, ctx: SkillContext) -> EchoOut: ...

Drop the module into any package listed in the registry's discovery paths and it is loaded
automatically - the core never needs editing to add skills.
"""

from __future__ import annotations

import enum
import inspect
import re
import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, get_type_hints

import httpx
from pydantic import BaseModel, ConfigDict

from jaxon.core.config import Settings

if TYPE_CHECKING:
    from jaxon.memory.store import MemoryStore

SKILL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


class Permission(enum.StrEnum):
    network = "network"  # outbound HTTP
    browser = "browser"  # drives a real browser
    memory_read = "memory_read"
    memory_write = "memory_write"
    side_effects = "side_effects"  # changes the outside world (email, payments, commits...)


class SkillInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkillOutput(BaseModel):
    pass


@dataclass
class SkillContext:
    settings: Settings
    memory: MemoryStore | None = None
    http: httpx.AsyncClient | None = None
    run_id: uuid.UUID | None = None
    agent_name: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)

    def http_client(self) -> httpx.AsyncClient:
        if self.http is None:
            self.http = httpx.AsyncClient(timeout=20.0, follow_redirects=False)
        return self.http


class SkillManifest(BaseModel):
    name: str
    description: str
    version: str
    tags: list[str]
    permissions: list[Permission]
    requires_approval: bool
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class Skill[InT: BaseModel, OutT: BaseModel](ABC):
    name: ClassVar[str]
    description: ClassVar[str]
    version: ClassVar[str] = "0.1.0"
    tags: ClassVar[tuple[str, ...]] = ()
    permissions: ClassVar[tuple[Permission, ...]] = ()
    # When True the agent loop will not execute it without a human approval step.
    requires_approval: ClassVar[bool] = False
    timeout_seconds: ClassVar[float | None] = None
    Input: ClassVar[type[BaseModel]]
    Output: ClassVar[type[BaseModel]]

    @abstractmethod
    async def run(self, inp: InT, ctx: SkillContext) -> OutT: ...

    @classmethod
    def manifest(cls) -> SkillManifest:
        return SkillManifest(
            name=cls.name,
            description=cls.description,
            version=cls.version,
            tags=list(cls.tags),
            permissions=list(cls.permissions),
            requires_approval=cls.requires_approval,
            input_schema=cls.Input.model_json_schema(),
            output_schema=cls.Output.model_json_schema(),
        )

    @classmethod
    def validate_definition(cls) -> None:
        for attr in ("name", "description", "Input", "Output"):
            if not hasattr(cls, attr):
                raise TypeError(f"Skill {cls.__qualname__} is missing '{attr}'")
        if not SKILL_NAME_RE.match(cls.name) or len(cls.name) > 64:
            raise TypeError(
                f"Invalid skill name '{cls.name}': use dotted lowercase, e.g. 'web.fetch_url'"
            )
        for attr in ("Input", "Output"):
            model = getattr(cls, attr)
            if not (isinstance(model, type) and issubclass(model, BaseModel)):
                raise TypeError(f"Skill {cls.name}.{attr} must be a pydantic BaseModel")


SkillFn = Callable[[Any, SkillContext], Awaitable[BaseModel]]


def skill(
    *,
    name: str,
    description: str,
    version: str = "0.1.0",
    tags: tuple[str, ...] = (),
    permissions: tuple[Permission, ...] = (),
    requires_approval: bool = False,
    timeout_seconds: float | None = None,
) -> Callable[[SkillFn], type[Skill[Any, Any]]]:
    """Turn `async def fn(inp: InModel, ctx) -> OutModel` into a Skill class."""

    def wrap(fn: SkillFn) -> type[Skill[Any, Any]]:
        if not inspect.iscoroutinefunction(fn):
            raise TypeError(f"@skill '{name}' must decorate an async function")
        hints = get_type_hints(fn)
        params = list(inspect.signature(fn).parameters)
        if not params or params[0] not in hints or "return" not in hints:
            raise TypeError(f"@skill '{name}' needs annotated input and return types")
        in_model, out_model = hints[params[0]], hints["return"]

        async def run(self: Skill[Any, Any], inp: Any, ctx: SkillContext) -> Any:
            return await fn(inp, ctx)

        cls = type(
            f"{fn.__name__.title().replace('_', '')}Skill",
            (Skill,),
            {
                "name": name,
                "description": description,
                "version": version,
                "tags": tags,
                "permissions": permissions,
                "requires_approval": requires_approval,
                "timeout_seconds": timeout_seconds,
                "Input": in_model,
                "Output": out_model,
                "run": run,
                "__module__": fn.__module__,
                "__doc__": fn.__doc__,
            },
        )
        cls.validate_definition()  # type: ignore[attr-defined]
        return cls

    return wrap
