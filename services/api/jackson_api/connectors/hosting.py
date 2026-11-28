"""Website hosting via Netlify: publishes approved sites and course pages (ADR-045).

Why Netlify: static HTML is one documented API call (a zip of the files), every site gets
HTTPS on *.netlify.app at once, a custom domain is one field, and the free plan is a hard
cap that never bills by surprise. (Cloudflare Pages' raw API upload is low-level and
undocumented outside Wrangler; Vercel adds a build pipeline we don't need.)

Checked against docs.netlify.com / open-api.netlify.com (2026-11-27):
- auth `Authorization: Bearer <NETLIFY_AUTH_TOKEN>` (a personal access token)
- GET  /api/v1/user                         -> the account (connection test)
- GET  /api/v1/sites?name=<name>&filter=all -> [{id, name, ssl_url, url, custom_domain}]
- POST /api/v1/sites {name, custom_domain?} -> the new site
- POST /api/v1/sites/{site_id}/deploys, Content-Type application/zip, body = zip of the
  files -> {id, state, ssl_url, deploy_ssl_url}; poll GET /api/v1/deploys/{id} until
  state == "ready" ("error" = failed)
- limits: 3 deploys a minute, 100 a day; the free plan pauses every site on the account
  when its monthly credits run out.

Publishing is a PRODUCTION change: the owner approves every launch (or a Launch approval
covers it). Without NETLIFY_AUTH_TOKEN the "live" URL is a mock:// address and nothing is
published.
"""

import io
import os
import re
import time
import zipfile

import httpx

from ..worker_errors import NonRetryableError

API = "https://api.netlify.com/api/v1"


def configured() -> bool:
    return bool(os.environ.get("NETLIFY_AUTH_TOKEN"))


def site_name(label: str) -> str:
    """A stable Netlify site name for one of Steady's sites (lowercase, a-z0-9 and -)."""
    prefix = (os.environ.get("NETLIFY_SITE_PREFIX") or "jackson").lower()
    s = re.sub(r"[^a-z0-9-]+", "-", f"{prefix}-{label}".lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)[:63].strip("-")


def zip_files(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path, body in sorted(files.items()):
            if path.startswith("/") or ".." in path.split("/"):
                raise NonRetryableError(f"bad file path {path!r}")
            z.writestr(path, body)
    return buf.getvalue()


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=60)


def _check(r: httpx.Response, what: str):
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()  # retryable (deploy limit is 3 a minute)
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        msg = body.get("message") if isinstance(body, dict) else None
        raise NonRetryableError(f"Netlify refused the {what}: {str(msg or r.status_code)[:300]}")
    return body


class NetlifyHost:
    backend = "netlify"

    def __init__(self, token: str, client: httpx.Client | None = None, sleep=time.sleep):
        self._h = {"Authorization": f"Bearer {token}"}
        self._client = client or _new_client()
        self._sleep = sleep

    def user(self) -> dict:
        return _check(self._client.get(f"{API}/user", headers=self._h), "token")

    def site(self, name: str, domain: str | None) -> dict:
        found = _check(self._client.get(f"{API}/sites", headers=self._h,
                                        params={"name": name, "filter": "all"}), "site list")
        for s in found if isinstance(found, list) else []:
            if s.get("name") == name:
                if domain and s.get("custom_domain") != domain:
                    s = _check(self._client.patch(f"{API}/sites/{s['id']}", headers=self._h,
                                                  json={"custom_domain": domain}), "domain")
                return s
        body = {"name": name}
        if domain:
            body["custom_domain"] = domain
        return _check(self._client.post(f"{API}/sites", headers=self._h, json=body), "new site")

    def publish(self, name: str, files: dict[str, str], domain: str | None = None,
                timeout_s: float = 180) -> dict:
        s = self.site(name, domain)
        d = _check(self._client.post(f"{API}/sites/{s['id']}/deploys",
                                     headers={**self._h, "Content-Type": "application/zip"},
                                     content=zip_files(files)), "deploy")
        deadline = time.monotonic() + timeout_s
        while d.get("state") != "ready":
            if d.get("state") == "error":
                raise NonRetryableError(f"Netlify deploy failed: {d.get('error_message')}")
            if time.monotonic() >= deadline:
                raise httpx.TimeoutException(f"Netlify deploy {d.get('id')} still {d.get('state')}")
            self._sleep(3)
            d = _check(self._client.get(f"{API}/deploys/{d['id']}", headers=self._h), "deploy")
        url = (f"https://{domain}/" if domain else None) or s.get("ssl_url") or d.get("ssl_url")
        return {"url": url, "site_id": s["id"], "deploy_id": d["id"],
                "netlify_url": s.get("ssl_url") or d.get("ssl_url")}

    def deploy(self, domain: str, number: int, files: dict[str, str]) -> str:
        """The site factory's interface (sites.launch_handler)."""
        return self.publish(site_name(domain.replace(".", "-")), files, domain)["url"]


class MockHost:
    """MOCK: nothing is deployed anywhere; the "live" URL is a mock:// address."""

    backend = "mock"

    def publish(self, name: str, files: dict[str, str], domain: str | None = None,
                timeout_s: float = 0) -> dict:
        zip_files(files)  # same validation as the real one
        return {"url": f"mock://sites/{name}/", "site_id": None, "deploy_id": None,
                "netlify_url": None}

    def deploy(self, domain: str, number: int, files: dict[str, str]) -> str:
        return f"mock://sites/{domain}/v{number}/"


def host():
    return NetlifyHost(os.environ["NETLIFY_AUTH_TOKEN"]) if configured() else MockHost()
