"""Provider-neutral model interface (spec vol. 01 "Executive cognition", phase 08, ADR-018).

No provider is assumed to be connected. Every response says which provider and model
actually served it and whether it was `live` or `mock`, so a mock can never pass for
real work.
"""

from dataclasses import dataclass, field
from typing import Literal, Protocol

from ..worker_errors import NonRetryableError

Mode = Literal["live", "mock"]

# USD per 1M tokens (input, output). Anthropic first-party list prices (claude-api skill,
# cached 2026-11-03). Re-check before relying on them for billing.
PRICES_USD_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-fable-5-2": (8.00, 40.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
}


class ModelRefused(NonRetryableError):
    """The model declined the request (`stop_reason: refusal`). Retrying won't help."""


class ModelRequestInvalid(NonRetryableError):
    """The provider rejected the request as malformed (4xx other than 429)."""


@dataclass(frozen=True)
class ModelRequest:
    system: str
    user: str
    max_tokens: int = 16000
    effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    json_schema: dict | None = None


@dataclass
class ModelResponse:
    text: str
    provider: str
    model: str  # the model that actually served the request (may be a fallback)
    mode: Mode
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd_micros: int = 0  # 1e-6 USD, integer to avoid float drift in the ledger
    stop_reason: str | None = None
    request_id: str | None = None
    notes: list[str] = field(default_factory=list)


def cost_micros(model: str, input_tokens: int, output_tokens: int,
                prices: dict[str, tuple[float, float]] | None = None) -> int | None:
    table = prices if prices is not None else PRICES_USD_PER_MTOK
    if model not in table:
        return None
    pin, pout = table[model]
    # tokens * USD/1M tokens * 1e6 micros/USD  ==  tokens * USD-per-Mtok
    return round(input_tokens * pin + output_tokens * pout)


class Provider(Protocol):
    name: str

    def available(self) -> bool: ...

    def complete(self, req: ModelRequest, model: str) -> ModelResponse: ...
