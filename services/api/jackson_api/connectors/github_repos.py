"""GitHub repos for the App/SaaS/Web Factory (ADR-050): every approved build gets its own
PRIVATE repository on Steady's GitHub, with the code committed in one commit.

GitHub REST API (docs.github.com, api version 2022-11-28):
- auth `Authorization: Bearer <GITHUB_APP_TOKEN>` (a fine-grained personal access token),
  `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`
- GET  /user                                  -> {login} (connection test)
- GET  /repos/{owner}/{repo}                  -> 404 if it doesn't exist yet
- POST /user/repos or /orgs/{org}/repos {name, private: true, auto_init: true, description}
- one commit with many files (Git Data API):
  GET /repos/{o}/{r}/git/ref/heads/{branch} -> {object: {sha}}
  GET /repos/{o}/{r}/git/commits/{sha}      -> {tree: {sha}}
  POST /repos/{o}/{r}/git/trees {base_tree, tree: [{path, mode: "100644", type: "blob",
       content}]}                           -> {sha}
  POST /repos/{o}/{r}/git/commits {message, tree, parents: [sha]} -> {sha}
  PATCH /repos/{o}/{r}/git/refs/heads/{branch} {sha}

Token permissions (fine-grained): Repository access "All repositories" (so it can create
new ones), Administration: Read and write, Contents: Read and write, Metadata: Read.
Repos are always private. Creating one and pushing to it is a production change the owner
approves.
"""

import os
import re
import time

import httpx

from ..worker_errors import NonRetryableError

API = "https://api.github.com"
MAX_FILES = 500
MAX_BYTES = 5_000_000


def configured() -> bool:
    return bool(os.environ.get("GITHUB_APP_TOKEN") and os.environ.get("GITHUB_OWNER"))


def repo_name(label: str) -> str:
    prefix = (os.environ.get("GITHUB_REPO_PREFIX") or "jackson").lower()
    s = re.sub(r"[^a-z0-9._-]+", "-", f"{prefix}-{label}".lower()).strip("-.")
    return re.sub(r"-{2,}", "-", s)[:100]


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=30)


def _check(r: httpx.Response, what: str) -> dict:
    if r.status_code == 429 or r.status_code >= 500 or (
            r.status_code == 403 and r.headers.get("x-ratelimit-remaining") == "0"):
        r.raise_for_status()  # retryable
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        msg = body.get("message") if isinstance(body, dict) else None
        raise NonRetryableError(f"GitHub refused the {what}: {str(msg or r.status_code)[:300]}")
    return body if isinstance(body, dict) else {"data": body}


class GitHub:
    def __init__(self, token: str, owner: str, client: httpx.Client | None = None):
        self._h = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        self.owner = owner.strip()
        self._client = client or _new_client()

    def user(self) -> dict:
        return _check(self._client.get(f"{API}/user", headers=self._h), "token")

    def ensure_repo(self, name: str, description: str) -> dict:
        r = self._client.get(f"{API}/repos/{self.owner}/{name}", headers=self._h)
        if r.status_code == 200:
            repo = r.json()
            if not repo.get("private"):
                raise NonRetryableError(f"{self.owner}/{name} exists and is PUBLIC: Jackson "
                                        "only pushes to private repos")
            return repo
        if r.status_code != 404:
            _check(r, "repo lookup")
        me = self.user().get("login", "")
        url = f"{API}/user/repos" if me.lower() == self.owner.lower() else \
            f"{API}/orgs/{self.owner}/repos"
        return _check(self._client.post(url, headers=self._h, json={
            "name": name, "private": True, "auto_init": True,
            "description": description[:350]}), "new repo")

    def commit_files(self, name: str, files: dict[str, str], message: str,
                     branch: str = "main") -> dict:
        if not files or len(files) > MAX_FILES:
            raise NonRetryableError(f"need 1..{MAX_FILES} files")
        if sum(len(v.encode()) for v in files.values()) > MAX_BYTES:
            raise NonRetryableError("files are larger than 5 MB in total")
        for p in files:
            if p.startswith("/") or ".." in p.split("/") or p.startswith(".git/"):
                raise NonRetryableError(f"bad file path {p!r}")
        base = f"{API}/repos/{self.owner}/{name}"
        ref = None
        for _ in range(5):  # a just-created repo's first commit can take a moment
            r = self._client.get(f"{base}/git/ref/heads/{branch}", headers=self._h)
            if r.status_code == 200:
                ref = r.json()
                break
            time.sleep(1)
        if ref is None:
            raise httpx.TimeoutException("the new repo's main branch isn't ready yet")
        head = ref["object"]["sha"]
        tree = _check(self._client.get(f"{base}/git/commits/{head}", headers=self._h),
                      "commit lookup")["tree"]["sha"]
        new_tree = _check(self._client.post(f"{base}/git/trees", headers=self._h, json={
            "base_tree": tree, "tree": [{"path": p, "mode": "100644", "type": "blob",
                                         "content": c} for p, c in sorted(files.items())]}),
            "file upload")["sha"]
        commit = _check(self._client.post(f"{base}/git/commits", headers=self._h, json={
            "message": message[:500], "tree": new_tree, "parents": [head]}), "commit")["sha"]
        _check(self._client.patch(f"{base}/git/refs/heads/{branch}", headers=self._h,
                                  json={"sha": commit}), "branch update")
        return {"commit": commit, "branch": branch}


def client() -> GitHub:
    if not configured():
        raise NonRetryableError("GitHub isn't connected: set GITHUB_APP_TOKEN and GITHUB_OWNER")
    return GitHub(os.environ["GITHUB_APP_TOKEN"], os.environ["GITHUB_OWNER"])
