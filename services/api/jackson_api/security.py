"""Security hardening (phase 20, ADR-030): response headers, request size limit, rate limit
for public endpoints, and production preflight checks that fail closed."""

import os
import re
import threading
import time
from collections import deque
from urllib.parse import urlparse

from starlette.responses import JSONResponse

PUBLIC_PREFIXES = ("/u/",)  # unauthenticated pages that need rate limiting
MAX_BODY_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", str(5 * 2**20)))


def is_production() -> bool:
    return os.environ.get("APP_ENV", "development").lower() == "production"


class RateLimiter:
    """Sliding-window limiter per key. In-process: each API replica limits on its own,
    which is enough to blunt abuse of the public unsubscribe page."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:  # bound memory
                self._hits = {k: v for k, v in self._hits.items() if v}
            return True


public_limiter = RateLimiter(limit=int(os.environ.get("PUBLIC_RATE_LIMIT", "30")),
                             window_s=60)


class SecurityMiddleware:
    """Pure ASGI middleware (works with streaming responses such as SSE)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        headers = dict(scope.get("headers") or [])
        length = headers.get(b"content-length")
        if length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return await JSONResponse({"detail": "request body too large"}, 413)(
                scope, receive, send)
        if path.startswith(PUBLIC_PREFIXES):
            client = (scope.get("client") or ("?", 0))[0]
            if not public_limiter.allow(client):
                return await JSONResponse({"detail": "too many requests"}, 429,
                                          headers={"Retry-After": "60"})(scope, receive, send)

        private = path.startswith("/tenants/")

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                h = list(message.get("headers") or [])
                present = {k.lower() for k, _ in h}

                def add(k: bytes, v: bytes):
                    if k not in present:
                        h.append((k, v))
                add(b"x-content-type-options", b"nosniff")
                add(b"referrer-policy", b"no-referrer")
                add(b"x-frame-options", b"DENY")
                add(b"permissions-policy", b"camera=(), microphone=(), geolocation=()")
                add(b"cross-origin-opener-policy", b"same-origin")
                if private:
                    add(b"cache-control", b"no-store")
                if is_production():
                    add(b"strict-transport-security", b"max-age=31536000; includeSubDomains")
                message["headers"] = h
            await send(message)

        await self.app(scope, receive, send_with_headers)


# --- Production preflight -----------------------------------------------------------------------

def _https(url: str | None) -> bool:
    return bool(url) and urlparse(url).scheme == "https"


def preflight(env: dict | None = None) -> list[dict]:
    """Checks that must pass before Jackson runs in production. Names only: no secret value
    is ever printed."""
    e = os.environ if env is None else env
    checks = []

    def check(name: str, ok: bool, detail: str):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    db = e.get("DATABASE_URL") or ""
    user = urlparse(db.replace("+psycopg", "")).username if db else None
    check("database_url", bool(db), "DATABASE_URL is set")
    check("least_privilege_db_role", user == "jackson_app" or (
        user is not None and user not in ("postgres", "jackson", "root")),
        "API connects as the least-privilege role, not the schema owner/superuser")
    wdb = e.get("WORKER_DATABASE_URL") or ""
    wuser = urlparse(wdb.replace("+psycopg", "")).username if wdb else None
    check("worker_db_role", bool(wuser) and wuser not in ("postgres", "jackson", "root",
                                                          user),
          "worker connects as its own least-privilege role (jackson_worker)")
    check("auth_configured", _https(e.get("AUTH_ISSUER")) and _https(e.get("AUTH_JWKS_URL")),
          "AUTH_ISSUER and AUTH_JWKS_URL are https URLs from the auth provider")
    check("authorized_parties", bool((e.get("AUTH_AUTHORIZED_PARTIES") or "").strip()),
          "AUTH_AUTHORIZED_PARTIES lists the console origin(s)")
    check("unsubscribe_secret", len(e.get("UNSUBSCRIBE_SECRET") or "") >= 32,
          "UNSUBSCRIBE_SECRET has 32+ characters")
    check("public_base_url", _https(e.get("PUBLIC_BASE_URL")),
          "PUBLIC_BASE_URL is the https address of the API")
    art = e.get("ARTIFACT_DIR") or ""
    check("durable_artifacts", bool(e.get("S3_BUCKET")) or (art.startswith("/") and
                                                           not art.startswith("/tmp")),
          "artifacts go to S3 or an absolute, persistent volume (not ./ or /tmp)")
    check("model_budget", re.fullmatch(r"\d+(\.\d+)?",
                                       e.get("MODEL_MONTHLY_BUDGET_USD", "20")) is not None,
          "MODEL_MONTHLY_BUDGET_USD is a number")
    check("no_unsandboxed_code", e.get("CODE_SANDBOX", "") != "process",
          "code.run_python is off in production until a container sandbox exists")
    return checks


def assert_production_ready() -> None:
    """Called at API start-up: in production, refuse to start with an unsafe config."""
    if not is_production():
        return
    failed = [c["check"] for c in preflight() if not c["ok"]]
    if failed:
        raise RuntimeError("refusing to start in production; failed checks: "
                           + ", ".join(failed) + " (run: python -m jackson_api.preflight)")
