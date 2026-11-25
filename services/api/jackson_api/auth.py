"""Bearer-token verification against a hosted OIDC/JWT provider (ADR-012).

Provider-agnostic: works with Clerk (recommended), Auth0, WorkOS, etc. — anything
that signs RS256 JWTs and publishes a JWKS. The API never sees passwords.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt

KeyResolver = Callable[[str], Any]


class AuthError(Exception):
    """Token missing, malformed, expired, or from the wrong issuer/party."""


@dataclass(frozen=True)
class AuthConfig:
    issuer: str
    jwks_url: str
    audience: str | None = None
    authorized_parties: frozenset[str] = frozenset()


class TokenVerifier:
    def __init__(self, config: AuthConfig, key_resolver: KeyResolver | None = None):
        self.config = config
        if key_resolver is None:
            client = jwt.PyJWKClient(config.jwks_url, cache_keys=True, lifespan=300)
            key_resolver = lambda token: client.get_signing_key_from_jwt(token).key  # noqa: E731
        self._resolve_key = key_resolver

    def verify(self, token: str) -> dict[str, Any]:
        try:
            key = self._resolve_key(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=self.config.issuer,
                audience=self.config.audience,
                leeway=5,
                options={
                    "require": ["exp", "iat", "iss", "sub"],
                    "verify_aud": self.config.audience is not None,
                },
            )
        except jwt.PyJWTError as exc:
            raise AuthError(type(exc).__name__) from exc

        # Clerk puts the requesting origin in `azp`; reject tokens minted for other sites.
        if self.config.authorized_parties and claims.get("azp") not in (
            self.config.authorized_parties
        ):
            raise AuthError("unauthorized party")
        return claims
