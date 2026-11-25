"""Visual sandbox browser sessions (spec vol. 02 "Visual work", phase 11, ADR-021).

An agent opens a session with `with ctx.browser() as b:`, then calls goto / text / click /
fill / screenshot. Every step:
  1. checks the owner's control (pause / take over: the agent waits; terminate: it stops);
  2. passes policy (reading is `read`; click/fill are `sandbox_change`, autonomy level 2+);
  3. runs, with EVERY network request (including images and scripts) limited to the grant's
     allowed domains;
  4. saves a screenshot with password and sensitive fields masked, updates the session
     (current URL, title, last action) and emits `browser.screenshot` on the live event
     stream, so the owner can watch.
Page text is UNTRUSTED. Text inside [data-sensitive] elements is removed before it
reaches the agent.

"Take over" pauses the agent and shows the latest screenshot. Live remote control of
the page (VNC-style) isn't built; that comes with the console (phase 19).
"""

import os
import time
import uuid
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright
from sqlalchemy import func, select

from .models import Artifact, BrowserSession, RunEvent
from .policy import Action, Category
from .tools.redact import redact_text
from .worker_errors import NonRetryableError

SENSITIVE = ("input[type=password], [data-sensitive], [autocomplete^='cc-'], "
             "input[name*='card' i], input[name*='ssn' i]")


def _allowed(url: str, domains: list[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if urlparse(url).scheme in ("data", "about", "blob"):
        return True
    return any(host == d or host.endswith("." + d) for d in domains)


class BrowserHandle:
    def __init__(self, worker, task_id: uuid.UUID, allowed_domains: list[str],
                 poll_s: float = 0.25, max_wait_s: float = 600):
        self.w, self.task_id = worker, task_id
        self.domains = [d.lower() for d in allowed_domains]
        self.poll_s, self.max_wait_s = poll_s, max_wait_s
        self.session_id: uuid.UUID | None = None
        self.blocked: list[str] = []
        self._pw = self._browser = self.page = None

    # --- lifecycle -----------------------------------------------------------------------

    def __enter__(self) -> "BrowserHandle":
        self.w._authorize(self.task_id, Action(Category.READ, "open sandbox browser"))
        with self.w.maker() as db:
            task, _ = self.w._locked(db, self.task_id)
            s = BrowserSession(tenant_id=task.tenant_id, run_id=task.run_id, task_id=task.id,
                               allowed_domains=self.domains, last_action="opened")
            db.add(s)
            db.flush()
            self.session_id, self.tenant_id, self.run_id = s.id, task.tenant_id, task.run_id
            self._emit(db, "browser.opened", allowed_domains=self.domains)
            db.commit()
        self._pw = sync_playwright().start()
        exe = os.environ.get("BROWSER_EXECUTABLE")
        self._browser = self._pw.chromium.launch(
            executable_path=exe or None, args=["--no-sandbox"] if exe else [])
        ctx = self._browser.new_context(viewport={"width": 1280, "height": 800},
                                        accept_downloads=False, service_workers="block")
        ctx.route("**/*", self._route)
        self.page = ctx.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self._browser:
                self._browser.close()
            if self._pw:
                self._pw.stop()
        finally:
            with self.w.maker() as db:
                s = db.get(BrowserSession, self.session_id)
                if s is not None and s.status != "terminated":
                    s.status = "closed"
                    s.ended_at = func.now()
                    s.last_action = "closed"
                self._emit(db, "browser.closed", blocked_requests=len(self.blocked))
                db.commit()

    def _route(self, route) -> None:
        if _allowed(route.request.url, self.domains):
            route.continue_()
        else:
            self.blocked.append(urlparse(route.request.url).hostname or "?")
            route.abort("blockedbyclient")

    # --- agent actions -------------------------------------------------------------------

    def goto(self, url: str) -> dict:
        if urlparse(url).scheme not in ("http", "https") or not _allowed(url, self.domains):
            raise NonRetryableError(f"{url} is not on this browser grant's allowlist")
        self._gate(Category.READ, f"goto {redact_text(url)}")
        resp = self.page.goto(url, wait_until="domcontentloaded", timeout=15_000)
        return self._after("goto", status=resp.status if resp else None)

    def text(self) -> dict:
        self._gate(Category.READ, "read page text")
        txt = self.page.evaluate(
            """sel => { const c = document.body.cloneNode(true);
                        c.querySelectorAll(sel).forEach(e => e.remove());
                        return c.innerText; }""", SENSITIVE)
        return {"url": redact_text(self.page.url), "text": redact_text(txt)[:50_000],
                "untrusted": True}

    def click(self, selector: str) -> dict:
        self._gate(Category.SANDBOX_CHANGE, f"click {selector}")
        self.page.click(selector, timeout=10_000)
        self.page.wait_for_load_state("domcontentloaded")
        return self._after(f"click {selector}")

    def fill(self, selector: str, value: str) -> dict:
        self._gate(Category.SANDBOX_CHANGE, f"fill {selector}")
        self.page.fill(selector, value, timeout=10_000)
        return self._after(f"fill {selector}")  # the value itself is never logged

    def screenshot(self) -> dict:
        self._gate(Category.READ, "screenshot")
        return self._after("screenshot")

    # --- internals -----------------------------------------------------------------------

    def _gate(self, category: Category, what: str) -> None:
        """Honor owner controls, then policy, before every action."""
        waited = 0.0
        while True:
            with self.w.maker() as db:
                s = db.get(BrowserSession, self.session_id)
                control = s.control
                if control == "terminate":
                    s.status, s.ended_at, s.last_action = "terminated", func.now(), what
                    self._emit(db, "browser.terminated")
                    db.commit()
                    raise NonRetryableError("browser session terminated by owner")
                if control in ("pause", "takeover"):
                    new = "paused" if control == "pause" else "taken_over"
                    if s.status != new:
                        s.status = new
                        self._emit(db, f"browser.{new}")
                    db.commit()
                else:
                    if s.status != "active":
                        s.status = "active"
                        self._emit(db, "browser.resumed")
                    db.commit()
                    break
            if waited >= self.max_wait_s:
                raise NonRetryableError("browser session stayed paused too long")
            self.w._heartbeat(self.task_id)  # keep the task's lease while waiting
            time.sleep(self.poll_s)
            waited += self.poll_s
        self.w._authorize(self.task_id, Action(category, f"browser: {what}"))

    def _after(self, action: str, **extra) -> dict:
        mask = self.page.locator(SENSITIVE)
        masked = mask.count()
        png = self.page.screenshot(mask=[mask], full_page=False)
        key, sha, size = self.w.artifacts.put(self.tenant_id, png)
        url, title = redact_text(self.page.url), redact_text(self.page.title())
        with self.w.maker() as db:
            art = Artifact(tenant_id=self.tenant_id, run_id=self.run_id, task_id=self.task_id,
                           kind="screenshot", content_type="image/png", size_bytes=size,
                           sha256=sha, storage_key=key,
                           meta={"url": url, "action": action, "masked_elements": masked,
                                 "session_id": str(self.session_id)})
            db.add(art)
            db.flush()
            s = db.scalar(select(BrowserSession).where(BrowserSession.id == self.session_id))
            s.current_url, s.title, s.last_action = url, title, action
            s.last_screenshot_id = art.id
            self._emit(db, "browser.screenshot", artifact_id=str(art.id), url=url,
                       title=title, action=action, masked_elements=masked)
            db.commit()
            return {"url": url, "title": title, "screenshot_artifact_id": str(art.id),
                    "masked_elements": masked, **extra}

    def _emit(self, db, type_: str, **data) -> None:
        db.add(RunEvent(tenant_id=self.tenant_id, run_id=self.run_id, type=type_,
                        data={"session_id": str(self.session_id),
                              "task_id": str(self.task_id), **data}))
