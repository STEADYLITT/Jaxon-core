"""Provider registry: add a provider by registering a factory, no core changes needed."""

from __future__ import annotations

from collections.abc import Callable

from jaxon.core.config import Settings
from jaxon.core.errors import ConfigurationError
from jaxon.providers.base import ModelProvider

ProviderFactory = Callable[[Settings], ModelProvider]
_FACTORIES: dict[str, ProviderFactory] = {}


def register_provider(name: str, factory: ProviderFactory) -> None:
    _FACTORIES[name] = factory


def available_providers() -> list[str]:
    return sorted(_FACTORIES)


def create_provider(settings: Settings, name: str | None = None) -> ModelProvider:
    key = name or settings.model_provider
    factory = _FACTORIES.get(key)
    if factory is None:
        raise ConfigurationError(
            f"Unknown model provider '{key}'", details={"available": available_providers()}
        )
    return factory(settings)


def _anthropic(settings: Settings) -> ModelProvider:
    from jaxon.providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider(settings)


register_provider("anthropic", _anthropic)
