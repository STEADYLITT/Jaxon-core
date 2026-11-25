"""Typed tool registry (spec vol. 02 "Body and tools", phase 10, ADR-020).

Each tool declares: input schema (pydantic), policy category, estimated cost, timeout,
whether it's idempotent, how its result is verified, and its mode (live/sandbox/mock).
Handlers never call tools directly. They go through `ctx.use_tool()`, the gateway in
worker.py, which enforces grants, schema, policy, budget, idempotency, timeouts and
redaction, and logs every call.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from ..policy import Category

Mode = Literal["live", "sandbox", "mock"]


@dataclass(frozen=True)
class ToolEnv:
    """What a tool may see: its grant config, never secrets or DB handles."""

    grant_config: dict
    idempotency_key: str


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    category: Category
    run: Callable[[BaseModel, ToolEnv], dict]
    cost_cents: int = 0
    timeout_s: float = 10.0
    idempotent: bool = True
    verification: str = ""
    mode: Mode = "live"
    max_output_chars: int = 50_000

    def describe(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "category": self.category.value, "cost_cents": self.cost_cents,
            "timeout_s": self.timeout_s, "idempotent": self.idempotent,
            "verification": self.verification, "mode": self.mode,
        }


REGISTRY: dict[str, ToolSpec] = {}


def register(spec: ToolSpec) -> ToolSpec:
    if spec.name in REGISTRY:
        raise ValueError(f"tool {spec.name} already registered")
    REGISTRY[spec.name] = spec
    return spec


def get(name: str) -> ToolSpec | None:
    _load_builtins()
    return REGISTRY.get(name)


def all_tools() -> list[ToolSpec]:
    _load_builtins()
    return sorted(REGISTRY.values(), key=lambda t: t.name)


_loaded = False


def _load_builtins() -> None:
    global _loaded
    if not _loaded:
        _loaded = True
        from . import builtin  # noqa: F401  (registers on import)
