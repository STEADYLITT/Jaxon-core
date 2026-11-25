"""Pick a provider + model + effort for a task profile (vol. 01: by task, cost, latency).

Defaults keep every profile on Claude Opus 5.5 and vary only effort. Lower effort on
the top model is the first cost lever, and it keeps one prompt-cache namespace. Owners
can change routes with JACKSON_MODEL_PROVIDER / JACKSON_MODEL_<PROFILE> env vars.
"Measured quality" routing arrives with evals in phase 12.

If the chosen provider isn't configured, the router returns the MOCK provider with a note
saying why. It never pretends a mock is live.
"""

import os
from dataclasses import dataclass

from .anthropic_provider import AnthropicProvider
from .base import Provider
from .mock_provider import MockProvider
from .openai_provider import OpenAICompatibleProvider

DEFAULT_ROUTES: dict[str, tuple[str, str, str]] = {
    # profile:  (provider, model, effort)
    "planning": ("anthropic", "claude-opus-5-5", "high"),
    "standard": ("anthropic", "claude-opus-5-5", "medium"),
    "fast": ("anthropic", "claude-opus-5-5", "low"),
}


@dataclass
class Route:
    provider: Provider
    model: str
    effort: str
    reason: str


class ModelRouter:
    def __init__(self, providers: dict[str, Provider] | None = None,
                 routes: dict[str, tuple[str, str, str]] | None = None):
        self.providers: dict[str, Provider] = providers if providers is not None else {
            "anthropic": AnthropicProvider(),
            "openai": OpenAICompatibleProvider.openai_from_env(),
            "local": OpenAICompatibleProvider.local_from_env(),
        }
        self.providers.setdefault("mock", MockProvider())
        self.routes = dict(routes or DEFAULT_ROUTES)

    def select(self, profile: str = "standard") -> Route:
        provider_name, model, effort = self.routes.get(profile, self.routes["standard"])
        forced = os.environ.get("JACKSON_MODEL_PROVIDER")
        if forced:
            provider_name = forced
            model = os.environ.get(f"JACKSON_MODEL_{profile.upper()}") or {
                "anthropic": model,
                "openai": getattr(self.providers.get("openai"), "default_model", None),
                "local": getattr(self.providers.get("local"), "default_model", None),
                "mock": "mock",
            }.get(provider_name) or model
        provider = self.providers.get(provider_name)
        if provider is None or not provider.available():
            return Route(self.providers["mock"], "mock", effort,
                         f"{provider_name} is not configured; using MOCK provider")
        return Route(provider, model, effort, f"profile '{profile}' -> {provider_name}/{model}")
