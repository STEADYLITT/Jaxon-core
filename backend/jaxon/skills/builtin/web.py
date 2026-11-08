from __future__ import annotations

from html.parser import HTMLParser
from typing import ClassVar

import httpx
from pydantic import Field

from jaxon.core.errors import DependencyUnavailable, ValidationFailed
from jaxon.skills.base import Permission, SkillContext, SkillInput, SkillOutput, skill
from jaxon.skills.builtin._policy import check_url_allowed


class _TextExtractor(HTMLParser):
    SKIP: ClassVar[set[str]] = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        elif tag in self.SKIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag in self.SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.parts.append(data.strip())


def html_to_text(html: str) -> tuple[str, str]:
    p = _TextExtractor()
    p.feed(html)
    return p.title.strip(), "\n".join(p.parts)


class FetchIn(SkillInput):
    url: str = Field(description="http(s) URL on an authorized domain")
    max_chars: int = Field(default=20_000, ge=100, le=200_000)


class FetchOut(SkillOutput):
    url: str
    status_code: int
    content_type: str
    title: str
    text: str
    truncated: bool


@skill(
    name="web.fetch_url",
    description="Fetch a web page over HTTP(S) and return its readable text. Only domains "
    "listed in ALLOWED_WEB_DOMAINS can be fetched.",
    tags=("web", "research"),
    permissions=(Permission.network,),
    timeout_seconds=30,
)
async def fetch_url(inp: FetchIn, ctx: SkillContext) -> FetchOut:
    check_url_allowed(inp.url, ctx.settings.allowed_web_domains)
    client = ctx.http_client()
    limit = ctx.settings.web_fetch_max_bytes
    try:
        async with client.stream("GET", inp.url, headers={"User-Agent": "JaxonOS/0.1"}) as r:
            if r.is_redirect:
                raise ValidationFailed(
                    "Redirects are not followed; fetch the target URL directly",
                    details={"location": r.headers.get("location")},
                )
            body = b""
            async for chunk in r.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    body = body[:limit]
                    break
            ctype = r.headers.get("content-type", "")
            status = r.status_code
            encoding = r.encoding or "utf-8"
    except httpx.HTTPError as e:
        raise DependencyUnavailable(f"Fetch failed: {type(e).__name__}: {e}") from e
    raw = body.decode(encoding, errors="replace")
    title, text = html_to_text(raw) if "html" in ctype else ("", raw)
    truncated = len(text) > inp.max_chars
    return FetchOut(
        url=inp.url,
        status_code=status,
        content_type=ctype,
        title=title,
        text=text[: inp.max_chars],
        truncated=truncated,
    )
