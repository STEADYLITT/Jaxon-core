import httpx
import pytest

from jaxon.core.errors import ValidationFailed
from jaxon.skills.base import SkillContext
from jaxon.skills.builtin.calc import safe_eval
from jaxon.skills.builtin.web import html_to_text


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("1+2*3", 7),
        ("(1200*0.15)+sqrt(16)", 184.0),
        ("2**10", 1024),
        ("-5 // 2", -3),
        ("max(1, 9, 3)", 9),
        ("round(pi, 2)", 3.14),
    ],
)
def test_calc(expr, expected):
    assert safe_eval(expr) == expected


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('id')",
        "open('x')",
        "a+1",
        "1/0",
        "2**100000",
        "lambda: 1",
        "1 +",
        "True + 1",
    ],
)
def test_calc_rejects(expr):
    with pytest.raises(ValidationFailed):
        safe_eval(expr)


async def test_echo_and_time(registry, ctx):
    assert (await registry.invoke("core.echo", {"text": "hi"}, ctx)).output == {"text": "hi"}
    r = await registry.invoke("core.current_time", {"timezone": "America/New_York"}, ctx)
    assert r.ok and r.output["timezone"] == "America/New_York"
    r = await registry.invoke("core.current_time", {"timezone": "Mars/Base"}, ctx)
    assert r.error["code"] == "validation_failed"


async def test_extract_contacts(registry, ctx):
    text = (
        "Contact Jane at Jane.Doe@Acme.io or sales@acme.io. Visit https://acme.io/about. "
        "Call +1 415-555-0132. Dup: sales@acme.io"
    )
    r = await registry.invoke("text.extract_contacts", {"text": text}, ctx)
    assert r.output["emails"] == ["jane.doe@acme.io", "sales@acme.io"]
    assert r.output["urls"] == ["https://acme.io/about"]
    assert len(r.output["phones"]) == 1


def test_html_to_text():
    title, text = html_to_text(
        "<html><head><title>Hi</title><style>x{}</style></head>"
        "<body><h1>Hello</h1><script>evil()</script><p>World</p></body></html>"
    )
    assert title == "Hi" and text == "Hello\nWorld"


# --- web.fetch_url uses an httpx MockTransport (MOCK: no real network) ---------------------


def _mock_http(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_fetch_allowed_domain(registry, settings):
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.host == "www.example.com"
        return httpx.Response(200, html="<title>Ex</title><p>Example body</p>")

    ctx = SkillContext(settings=settings, http=_mock_http(handler))
    r = await registry.invoke("web.fetch_url", {"url": "https://www.example.com/x"}, ctx)
    assert r.ok, r.error
    assert r.output["title"] == "Ex" and r.output["text"] == "Example body"


async def test_fetch_denies_unlisted_domain(registry, settings):
    ctx = SkillContext(settings=settings, http=_mock_http(lambda r: httpx.Response(200)))
    for url in ("https://evil.test/", "https://example.com.evil.test/", "file:///etc/passwd"):
        r = await registry.invoke("web.fetch_url", {"url": url}, ctx)
        assert not r.ok and r.error["code"] in {"permission_denied", "validation_failed"}, url


async def test_fetch_does_not_follow_redirects(registry, settings):
    ctx = SkillContext(
        settings=settings,
        http=_mock_http(lambda r: httpx.Response(302, headers={"location": "https://evil.test/"})),
    )
    r = await registry.invoke("web.fetch_url", {"url": "https://example.com/"}, ctx)
    assert r.error["code"] == "validation_failed"


async def test_fetch_truncates(registry, settings):
    ctx = SkillContext(
        settings=settings,
        http=_mock_http(
            lambda r: httpx.Response(200, text="a" * 5000, headers={"content-type": "text/plain"})
        ),
    )
    r = await registry.invoke(
        "web.fetch_url", {"url": "https://example.com/", "max_chars": 100}, ctx
    )
    assert r.output["truncated"] and len(r.output["text"]) == 100


async def test_memory_skills_without_store(registry, ctx):
    r = await registry.invoke("memory.recall", {"query": "x"}, ctx)
    assert r.error["code"] == "dependency_unavailable"
