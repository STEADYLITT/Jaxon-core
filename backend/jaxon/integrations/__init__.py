"""External integrations.

Each integration reports whether its credentials are configured. "configured" is never
treated as "working": only a live test (tests/live) proves an integration works.
"""

from __future__ import annotations

from pydantic import BaseModel

from jaxon.core.config import Settings


class IntegrationStatus(BaseModel):
    name: str
    configured: bool
    required_env: list[str]
    verified: bool = False  # set only by an explicit live check
    notes: str = ""


def integration_statuses(settings: Settings, provider_configured: bool) -> list[IntegrationStatus]:
    return [
        IntegrationStatus(
            name="anthropic",
            configured=provider_configured,
            required_env=["ANTHROPIC_API_KEY"],
            notes="Model provider for agent runs.",
        ),
        IntegrationStatus(
            name="github",
            configured=bool(settings.github_token),
            required_env=["GITHUB_TOKEN"],
            notes="Planned: self-improvement via reviewed pull requests (Phase 3).",
        ),
        IntegrationStatus(
            name="web",
            configured=bool(settings.allowed_web_domains),
            required_env=["ALLOWED_WEB_DOMAINS"],
            notes="Domain allowlist for web.fetch_url / browser.page_text.",
        ),
    ]
