"""Derive the runtime settings a single-owner deployment needs from a handful of inputs
(ADR-031). Used by the container start script; anything the owner sets explicitly wins.

Inputs (names only):
  MIGRATION_DATABASE_URL   schema-owner connection (e.g. Railway's Postgres DATABASE_URL)
  CLERK_PUBLISHABLE_KEY    public key from the Clerk dashboard (pk_test_... / pk_live_...)
  RAILWAY_PUBLIC_DOMAIN    set by Railway once a public domain is generated
  DATA_DIR                 persistent volume mount (default /data)
Generated once and kept on the volume (never printed):
  app/worker database passwords, UNSUBSCRIBE_SECRET

`python -m jackson_api.deploy_env --export` prints shell `export` lines for start.sh.
"""

import base64
import json
import os
import secrets
import shlex
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

SECRET_NAMES = ("APP_DB_PASSWORD", "WORKER_DB_PASSWORD", "UNSUBSCRIBE_SECRET")


def data_dir(env=os.environ) -> Path:
    return Path(env.get("DATA_DIR", "/data"))


def load_or_create_secrets(env=os.environ) -> dict:
    """Secrets live in <DATA_DIR>/.jackson-secrets.json (mode 0600), created on first boot.
    A value set in the environment overrides the file."""
    path = data_dir(env) / ".jackson-secrets.json"
    stored = json.loads(path.read_text()) if path.exists() else {}
    changed = False
    for name in SECRET_NAMES:
        if not stored.get(name):
            stored[name] = secrets.token_urlsafe(32)
            changed = True
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(stored))
        os.chmod(tmp, 0o600)
        tmp.replace(path)
    return {n: env.get(n) or stored[n] for n in SECRET_NAMES}


def clerk_frontend_api(publishable_key: str | None) -> str | None:
    """Clerk encodes its Frontend API host in the publishable key: pk_<env>_<base64(host$)>
    (the same derivation Clerk's JavaScript quickstart uses)."""
    if not publishable_key or publishable_key.count("_") < 2:
        return None
    try:
        raw = publishable_key.split("_", 2)[2]
        host = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    host = host.rstrip("$")
    return host if host and "/" not in host and " " not in host else None


def _sqlalchemy(url: str) -> str:
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def with_user(url: str, user: str, password: str) -> str:
    parts = urlsplit(_sqlalchemy(url))
    host = parts.hostname or ""
    netloc = f"{quote(user)}:{quote(password, safe='')}@{host}" + (
        f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def derive(env=os.environ, secret_values: dict | None = None) -> dict:
    """Return only the variables that need setting (never overrides an explicit value)."""
    out = {}

    def put(name, value):
        if value and not env.get(name):
            out[name] = value

    owner = env.get("MIGRATION_DATABASE_URL")
    if owner:
        out_owner = _sqlalchemy(owner)
        if out_owner != owner:
            out["MIGRATION_DATABASE_URL"] = out_owner
        s = secret_values or {}
        if s.get("APP_DB_PASSWORD"):
            put("DATABASE_URL", with_user(owner, "jackson_app", s["APP_DB_PASSWORD"]))
        if s.get("WORKER_DB_PASSWORD"):
            put("WORKER_DATABASE_URL", with_user(owner, "jackson_worker",
                                                 s["WORKER_DB_PASSWORD"]))
        put("UNSUBSCRIBE_SECRET", s.get("UNSUBSCRIBE_SECRET"))
    if env.get("RAILWAY_PUBLIC_DOMAIN"):
        put("PUBLIC_BASE_URL", f"https://{env['RAILWAY_PUBLIC_DOMAIN']}")
    public = env.get("PUBLIC_BASE_URL") or out.get("PUBLIC_BASE_URL")
    fapi = clerk_frontend_api(env.get("CLERK_PUBLISHABLE_KEY"))
    if fapi:
        put("AUTH_ISSUER", f"https://{fapi}")
        put("AUTH_JWKS_URL", f"https://{fapi}/.well-known/jwks.json")
    put("AUTH_AUTHORIZED_PARTIES", public)
    put("ARTIFACT_DIR", str(data_dir(env) / "artifacts"))
    return out


def main(argv: list[str]) -> int:
    if "--export" not in argv:
        print("usage: python -m jackson_api.deploy_env --export")
        return 2
    values = derive(os.environ, load_or_create_secrets(os.environ)
                    if os.environ.get("MIGRATION_DATABASE_URL") else None)
    for k, v in values.items():
        print(f"export {k}={shlex.quote(v)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
