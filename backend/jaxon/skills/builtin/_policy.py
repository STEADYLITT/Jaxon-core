"""Shared outbound-access policy for web/browser skills (authorized automation only)."""

from __future__ import annotations

from urllib.parse import urlparse

from jaxon.core.errors import PermissionDenied, ValidationFailed


def check_url_allowed(url: str, allowed_domains: list[str]) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationFailed("Only http(s) URLs are allowed", details={"url": url})
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValidationFailed("URL has no host", details={"url": url})
    for d in allowed_domains:
        d = d.lower().strip().lstrip(".")
        if d and (host == d or host.endswith("." + d)):
            return host
    raise PermissionDenied(
        f"Host '{host}' is not in ALLOWED_WEB_DOMAINS",
        details={"host": host, "hint": "Add the domain to ALLOWED_WEB_DOMAINS to authorize it"},
    )
