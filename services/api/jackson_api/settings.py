"""Runtime configuration from environment variables (names listed in .env.example)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str | None
    auth_issuer: str | None
    auth_jwks_url: str | None
    auth_audience: str | None
    auth_authorized_parties: frozenset[str]

    @property
    def auth_configured(self) -> bool:
        return bool(self.auth_issuer and self.auth_jwks_url)


def load_settings() -> Settings:
    parties = os.environ.get("AUTH_AUTHORIZED_PARTIES", "")
    return Settings(
        database_url=os.environ.get("DATABASE_URL") or None,
        auth_issuer=os.environ.get("AUTH_ISSUER") or None,
        auth_jwks_url=os.environ.get("AUTH_JWKS_URL") or None,
        auth_audience=os.environ.get("AUTH_AUDIENCE") or None,
        auth_authorized_parties=frozenset(p.strip() for p in parties.split(",") if p.strip()),
    )
