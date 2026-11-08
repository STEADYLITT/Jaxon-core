"""REAL Playwright/Chromium against a local HTTP server (no external network)."""

import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.browser]


@pytest.fixture
def site(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<html><head><title>Local Test</title></head><body><p id='x'>static</p>"
        "<script>document.getElementById('x').textContent='rendered by js'</script></body></html>"
    )
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/index.html"
    srv.shutdown()


async def test_page_text_renders_js(registry, settings, site):
    pytest.importorskip("playwright")
    settings.allowed_web_domains = ["127.0.0.1"]
    settings.browser_executable_path = os.environ.get("BROWSER_EXECUTABLE_PATH")
    from jaxon.skills.base import SkillContext

    r = await registry.invoke("browser.page_text", {"url": site}, SkillContext(settings=settings))
    if not r.ok and "Executable doesn't exist" in r.error.get("message", ""):
        pytest.skip("No Chromium available for Playwright")
    assert r.ok, r.error
    assert r.output["title"] == "Local Test" and "rendered by js" in r.output["text"]


async def test_page_text_denied_without_allowlist(registry, settings, site):
    settings.allowed_web_domains = []
    from jaxon.skills.base import SkillContext

    r = await registry.invoke("browser.page_text", {"url": site}, SkillContext(settings=settings))
    assert r.error["code"] == "permission_denied"
