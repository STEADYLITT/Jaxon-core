"""Website factory (spec vol. 03, phase 17, ADR-027).

requirements -> template build (static HTML, no scripts, owner's words only, all escaped)
-> HTML + accessibility checks -> Playwright preview check (desktop + phone) -> a new,
immutable site version. Launching a version: domain verified (DNS TXT record) -> owner
approval (production_change, always) -> deploy through the hosting connector (MOCK until a
hosting account exists). The previous live version is kept for one-click rollback. A DB
trigger only lets tested versions go live.
"""

import html
import os
import re
import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser

import httpx

from .connectors.claims import check_claims
from .worker_errors import NonRetryableError

PAGES = {"index": "Home", "about": "About", "services": "Services", "contact": "Contact",
         "privacy": "Privacy"}
DOMAIN_RE = re.compile(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
PREVIEW_ORIGIN = "http://preview.jackson.test"


# --- Build --------------------------------------------------------------------------------------

def _e(s) -> str:
    return html.escape(str(s or ""), quote=True)


def _paras(text: str) -> str:
    return "".join(f"<p>{_e(p.strip())}</p>" for p in str(text or "").split("\n\n")
                   if p.strip())


def build_site(req: dict) -> dict[str, str]:
    """Render the site from the owner's requirements. Returns {path: html}."""
    name = req["business_name"]
    brand = req.get("brand") or {}
    primary = brand.get("primary", "#1f4e79")
    text, bg = brand.get("text", "#1a1a1a"), brand.get("background", "#ffffff")
    pages = ["index"] + [p for p in ("about", "services", "contact")
                         if p in req.get("pages", ["about", "services", "contact"])]
    pages.append("privacy")  # every site gets a privacy notice
    nav = "".join(f'<li><a href="{p}.html">{PAGES[p]}</a></li>' for p in pages
                  if p != "privacy")
    email = req.get("contact_email", "")
    css = (f"*{{box-sizing:border-box}}body{{margin:0;font-family:system-ui,sans-serif;"
           f"line-height:1.6;color:{text};background:{bg}}}"
           f"header,main,footer{{max-width:60rem;margin:0 auto;padding:1rem}}"
           f"nav ul{{list-style:none;display:flex;flex-wrap:wrap;gap:1rem;padding:0}}"
           f"a{{color:{primary}}}.cta{{display:inline-block;background:{primary};color:#fff;"
           f"padding:.75rem 1.25rem;border-radius:.375rem;text-decoration:none}}"
           f"img{{max-width:100%;height:auto}}")

    def page(slug: str, heading: str, body: str) -> str:
        title = name if slug == "index" else f"{PAGES[slug]} | {name}"
        desc = req.get("tagline") if slug == "index" else f"{PAGES[slug]} - {name}"
        return (f'<!doctype html><html lang="{_e(req.get("language", "en"))}"><head>'
                f'<meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width, initial-scale=1">'
                f"<title>{_e(title)}</title>"
                f'<meta name="description" content="{_e(desc)}"><style>{css}</style></head>'
                f'<body><header><a href="index.html">{_e(name)}</a>'
                f'<nav aria-label="Main"><ul>{nav}</ul></nav></header>'
                f"<main><h1>{_e(heading)}</h1>{body}</main>"
                f'<footer><p>&copy; {datetime.now(UTC).year} {_e(name)} · '
                f'<a href="privacy.html">Privacy notice</a>'
                + (f' · <a href="mailto:{_e(email)}">{_e(email)}</a>' if email else "")
                + "</p></footer></body></html>")

    files = {}
    services = req.get("services", [])
    home = (f"<p>{_e(req.get('tagline'))}</p>{_paras(req.get('intro', ''))}"
            + ('<p><a class="cta" href="contact.html">Get in touch</a></p>'
               if "contact" in pages else ""))
    files["index.html"] = page("index", name, home)
    if "about" in pages:
        files["about.html"] = page("about", f"About {name}", _paras(req.get("about", "")))
    if "services" in pages:
        items = "".join(f"<section><h2>{_e(s['name'])}</h2>{_paras(s.get('description'))}"
                        "</section>" for s in services)
        files["services.html"] = page("services", "Services", items or "<p>Coming soon.</p>")
    if "contact" in pages:
        files["contact.html"] = page("contact", "Contact", (
            f'<p>Email us at <a href="mailto:{_e(email)}">{_e(email)}</a>.</p>'
            if email else "<p>Contact details coming soon.</p>"))
    files["privacy.html"] = page("privacy", "Privacy notice", (
        f"<p>This notice explains what {_e(name)} collects through this website.</p>"
        "<h2>What we collect</h2><p>This site has no forms, cookies, analytics or "
        "tracking scripts. If you email us, we receive your email address and message and "
        "use them only to reply to you.</p>"
        "<h2>Your choices</h2><p>You can ask us to delete your messages at any time"
        + (f' by emailing <a href="mailto:{_e(email)}">{_e(email)}</a>' if email else "")
        + ".</p>"
        f"<p>Last updated {datetime.now(UTC).date().isoformat()}.</p>"))
    return files


# --- Static HTML + accessibility checks ---------------------------------------------------------

class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.doctype = False
        self.lang = None
        self.title = ""
        self.meta = {}
        self.headings: list[int] = []
        self.imgs_without_alt = 0
        self.links: list[str] = []
        self.scripts = 0
        self.inline_handlers = 0
        self.inputs: list[dict] = []
        self.label_for: set[str] = set()
        self._in_title = False

    def handle_decl(self, decl):
        self.doctype = self.doctype or decl.lower() == "doctype html"

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if any(k.startswith("on") for k in a):
            self.inline_handlers += 1
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and a.get("name"):
            self.meta[a["name"].lower()] = a.get("content", "")
        elif re.fullmatch(r"h[1-6]", tag):
            self.headings.append(int(tag[1]))
        elif tag == "img" and not a.get("alt", "").strip() and a.get("role") != "presentation":
            self.imgs_without_alt += 1
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])
        elif tag in ("script", "iframe", "object", "embed"):
            self.scripts += 1
        elif tag in ("input", "select", "textarea") and a.get("type") != "hidden":
            self.inputs.append(a)
        elif tag == "label" and a.get("for"):
            self.label_for.add(a["for"])

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def _luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    chans = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def static_checks(files: dict[str, str], req: dict) -> dict:
    """Per-page HTML/accessibility checks plus site-wide ones. {"passed", "problems", ...}"""
    problems: list[str] = []
    for path, src in files.items():
        p = _Page()
        p.feed(src)
        where = path
        if not p.doctype:
            problems.append(f"{where}: missing <!doctype html>")
        if not p.lang:
            problems.append(f"{where}: <html> needs a lang attribute")
        if not p.title.strip():
            problems.append(f"{where}: empty <title>")
        if not p.meta.get("description", "").strip():
            problems.append(f"{where}: missing meta description")
        if "width=device-width" not in p.meta.get("viewport", ""):
            problems.append(f"{where}: missing mobile viewport meta tag")
        if p.headings.count(1) != 1:
            problems.append(f"{where}: needs exactly one <h1> (has {p.headings.count(1)})")
        for prev, cur in zip(p.headings, p.headings[1:], strict=False):
            if cur > prev + 1:
                problems.append(f"{where}: heading jumps from h{prev} to h{cur}")
                break
        if p.imgs_without_alt:
            problems.append(f"{where}: {p.imgs_without_alt} image(s) without alt text")
        if p.scripts or p.inline_handlers:
            problems.append(f"{where}: scripts/embeds are not allowed in factory sites")
        for i in p.inputs:
            if not (i.get("aria-label") or (i.get("id") and i["id"] in p.label_for)):
                problems.append(f"{where}: form field without a label")
        for href in p.links:
            if href.startswith(("mailto:", "tel:", "#")):
                continue
            if href.startswith(("http://", "https://", "//")):
                continue  # external links are allowed; they aren't part of this build
            if href.split("#")[0] not in files:
                problems.append(f"{where}: broken link to {href}")
        if path != "privacy.html" and "privacy.html" not in p.links:
            problems.append(f"{where}: no link to the privacy notice")
    if "privacy.html" not in files:
        problems.append("site has no privacy notice")
    brand = req.get("brand") or {}
    text_ratio = contrast(brand.get("text", "#1a1a1a"), brand.get("background", "#ffffff"))
    link_ratio = contrast(brand.get("primary", "#1f4e79"), brand.get("background", "#ffffff"))
    button_ratio = contrast("#ffffff", brand.get("primary", "#1f4e79"))
    for label, ratio in (("text on background", text_ratio), ("links on background",
                         link_ratio), ("button text on brand colour", button_ratio)):
        if ratio < 4.5:
            problems.append(f"colour contrast too low for {label}: {ratio}:1 (needs 4.5:1)")
    copy = " ".join([req.get("business_name", ""), req.get("tagline", ""), req.get("intro", ""),
                     req.get("about", "")] + [f"{s.get('name')} {s.get('description', '')}"
                                              for s in req.get("services", [])])
    problems += [f"copy: {c}" for c in check_claims(copy)]
    return {"passed": not problems, "problems": problems, "pages": sorted(files),
            "contrast": {"text": text_ratio, "links": link_ratio, "button": button_ratio}}


# --- Playwright preview check -------------------------------------------------------------------

def preview_check(files: dict[str, str]) -> dict:
    """Load every page in real Chromium, at desktop and phone sizes, from an in-memory
    origin. Fails on console errors, failed requests, a hidden <h1>, sideways scrolling on
    a phone, or a nav link that doesn't open a page. Returns a screenshot of the phone home
    page too."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:  # pragma: no cover - the worker image installs it
        return {"ran": False, "passed": False, "problems": ["Playwright is not installed"]}
    problems: list[str] = []
    screenshot = b""
    try:
        with sync_playwright() as pw:
            exe = os.environ.get("BROWSER_EXECUTABLE")
            browser = pw.chromium.launch(executable_path=exe or None,
                                         args=["--no-sandbox"] if exe else [])
            try:
                for label, vp in (("desktop", {"width": 1280, "height": 800}),
                                  ("phone", {"width": 375, "height": 740})):
                    ctx = browser.new_context(viewport=vp, java_script_enabled=True)
                    page = ctx.new_page()
                    errors: list[str] = []
                    page.on("console", lambda m, e=errors: m.type == "error" and e.append(m.text))
                    page.on("pageerror", lambda exc, e=errors: e.append(str(exc)))

                    def serve(route, request, errs=errors):
                        path = request.url.removeprefix(PREVIEW_ORIGIN + "/").split("?")[0]
                        if request.url.startswith(PREVIEW_ORIGIN) and (path or "index.html") \
                                in files:
                            route.fulfill(status=200, content_type="text/html; charset=utf-8",
                                          body=files[path or "index.html"])
                        else:
                            errs.append(f"blocked or missing request: {request.url}")
                            route.abort()
                    page.route("**/*", serve)
                    for path in sorted(files):
                        page.goto(f"{PREVIEW_ORIGIN}/{path}")
                        if not page.locator("h1").first.is_visible():
                            problems.append(f"{label} {path}: <h1> not visible")
                        if label == "phone" and page.evaluate(
                                "document.documentElement.scrollWidth > window.innerWidth + 1"):
                            problems.append(f"{label} {path}: page scrolls sideways on a phone")
                    page.goto(f"{PREVIEW_ORIGIN}/index.html")
                    hrefs = page.eval_on_selector_all("nav a", "els => els.map(e => "
                                                      "e.getAttribute('href'))")
                    for href in hrefs:
                        page.goto(f"{PREVIEW_ORIGIN}/index.html")
                        page.click(f'nav a[href="{href}"]')
                        page.wait_for_load_state()
                        if not page.url.endswith("/" + href):
                            problems.append(f"{label}: nav link {href} didn't open")
                    if label == "phone":
                        page.goto(f"{PREVIEW_ORIGIN}/index.html")
                        screenshot = page.screenshot(full_page=True)
                    problems += [f"{label}: {e}" for e in errors]
                    ctx.close()
            finally:
                browser.close()
    except Exception as exc:  # noqa: BLE001 - a broken browser means the preview didn't pass
        return {"ran": False, "passed": False, "problems": [f"preview failed to run: {exc}"[:300]]}
    return {"ran": True, "passed": not problems, "problems": problems,
            "_screenshot": screenshot}


# --- Domain verification + hosting --------------------------------------------------------------

def txt_record(domain: str, token: str) -> dict:
    return {"type": "TXT", "name": f"_jackson-verify.{domain}", "value": f"jackson-verify={token}"}


class DnsVerifier:
    """Real check over DNS-over-HTTPS (JSON API). No credentials needed."""

    mode = "dns"

    def __init__(self, client: httpx.Client | None = None,
                 url: str = "https://cloudflare-dns.com/dns-query"):
        self._client, self.url = client or httpx.Client(timeout=10), url

    def verify(self, domain: str, token: str) -> dict:
        rec = txt_record(domain, token)
        r = self._client.get(self.url, params={"name": rec["name"], "type": "TXT"},
                             headers={"accept": "application/dns-json"})
        if r.status_code >= 500 or r.status_code == 429:
            r.raise_for_status()  # retryable
        answers = [a.get("data", "").strip('"') for a in r.json().get("Answer", [])
                   if a.get("type") == 16]
        return {"verified": rec["value"] in answers, "mode": self.mode, "record": rec}


class MockDnsVerifier:
    """MOCK: reports the domain as verified without looking it up. Clearly labeled."""

    mode = "mock"

    def verify(self, domain: str, token: str) -> dict:
        return {"verified": True, "mode": self.mode, "record": txt_record(domain, token)}


def domain_verifier():
    return DnsVerifier() if os.environ.get("SITE_DOMAIN_VERIFY") == "dns" else MockDnsVerifier()


class MockHost:
    """MOCK hosting connector: nothing is deployed anywhere. Until a hosting account exists
    the "live" URL is a mock:// address."""

    backend = "mock"

    def deploy(self, domain: str, number: int, files: dict[str, str]) -> str:
        return f"mock://sites/{domain}/v{number}/"


# --- Worker handlers ----------------------------------------------------------------------------

def build_handler(ctx):
    from sqlalchemy import func, select

    from .models import Artifact, SiteProject, SiteVersion
    from .worker import TaskResult

    sid = uuid.UUID((ctx.input or {})["site_id"])
    with ctx.db_session() as db:
        site = db.get(SiteProject, sid)
        if site is None or site.tenant_id != ctx.tenant_id:
            raise NonRetryableError("site not found")
        req = dict(site.requirements)
        db.rollback()
    files = build_site(req)
    static = static_checks(files, req)
    preview = preview_check(files)
    shot = preview.pop("_screenshot", b"")
    checks = {"static": static, "preview": preview}
    passed = static["passed"] and preview["passed"]
    with ctx.db_session() as db:
        number = (db.scalar(select(func.max(SiteVersion.number))
                            .where(SiteVersion.site_id == sid)) or 0) + 1
        v = SiteVersion(tenant_id=ctx.tenant_id, site_id=sid, number=number, run_id=ctx.run_id,
                        files=files, checks=checks, passed=passed)
        db.add(v)
        db.flush()
        shot_id = None
        if shot:
            key, sha, size = ctx.artifacts.put(ctx.tenant_id, shot)
            art = Artifact(tenant_id=ctx.tenant_id, run_id=ctx.run_id, task_id=ctx.task_id,
                           kind="screenshot", content_type="image/png", size_bytes=size,
                           sha256=sha, storage_key=key,
                           meta={"type": "site_preview", "site_version_id": str(v.id)})
            db.add(art)
            db.flush()
            shot_id = str(art.id)
        site = db.get(SiteProject, sid)
        if passed and site.status == "draft":
            site.status = "ready"
        vid = str(v.id)
        db.commit()
    return TaskResult({"site_id": str(sid), "version_id": vid, "number": number,
                       "passed": passed, "problems": static["problems"] + preview["problems"],
                       "preview_screenshot_artifact_id": shot_id})


def launch_handler(ctx):
    from sqlalchemy import func

    from .models import RunEvent, SiteProject, SiteVersion
    from .policy import Action, Category
    from .worker import TaskResult

    inp = ctx.input or {}
    sid, vid = uuid.UUID(inp["site_id"]), uuid.UUID(inp["version_id"])
    with ctx.db_session() as db:
        site, v = db.get(SiteProject, sid), db.get(SiteVersion, vid)
        if site is None or site.tenant_id != ctx.tenant_id or v is None or v.site_id != sid:
            raise NonRetryableError("site version not found")
        if not v.passed:
            raise NonRetryableError(f"version {v.number} did not pass its tests")
        if not site.domain or not site.domain_token:
            raise NonRetryableError("set a domain before launching")
        domain, token, number, files = site.domain, site.domain_token, v.number, dict(v.files)
        db.rollback()
    check = domain_verifier().verify(domain, token)
    if not check["verified"]:
        rec = check["record"]
        raise NonRetryableError(f"domain not verified: add a {rec['type']} record "
                                f"{rec['name']} = {rec['value']} and launch again")
    # Going live is a production change: ALWAYS needs the owner's approval.
    ctx.authorize(Action(Category.PRODUCTION_CHANGE,
                         f"launch site {sid} version {number} on {domain}"))
    host = MockHost()
    url = host.deploy(domain, number, files)
    with ctx.db_session() as db:
        site = db.get(SiteProject, sid)
        prev = site.live_version_id if site.live_version_id != vid else site.previous_version_id
        site.previous_version_id, site.live_version_id = prev, vid
        site.status, site.live_url, site.launched_at = "live", url, func.now()
        db.add(RunEvent(tenant_id=ctx.tenant_id, run_id=ctx.run_id, type="site.launched",
                        data={"site_id": str(sid), "version": number, "domain": domain,
                              "url": url, "host": host.backend,
                              "domain_check": check["mode"]}))
        db.commit()
    return TaskResult({"site_id": str(sid), "version": number, "url": url,
                       "host": host.backend, "domain_check": check["mode"],
                       "rollback_to": str(prev) if prev else None})

