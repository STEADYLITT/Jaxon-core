"""Secret redaction for anything a tool takes in or gives back (spec: "secrets absent from
logs", "redact secrets and sensitive fields")."""

import os
import re

REDACTED = "[REDACTED]"
_SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)", re.I)
_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),              # OpenAI/Anthropic-style keys
    re.compile(r"shpat_[a-f0-9]{32}"),                 # Shopify admin tokens
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),         # GitHub tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),                   # AWS access key ids
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),  # JWTs
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(password|passwd|secret|api[_-]?key|token)(\"?\s*[:=]\s*\"?)[^\s\"',}]{6,}"),
]


def _env_secrets() -> list[str]:
    return [v for k, v in os.environ.items() if _SECRET_ENV.search(k) and len(v) >= 8]


def redact_text(s: str) -> str:
    for v in _env_secrets():
        s = s.replace(v, REDACTED)
    for p in _PATTERNS:
        if p.groups >= 2:
            s = p.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", s)
        else:
            s = p.sub(REDACTED, s)
    return s


def redact(obj):
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, dict):
        return {k: (REDACTED if _SECRET_ENV.search(str(k)) and isinstance(v, str) else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [redact(v) for v in obj]
    return obj
