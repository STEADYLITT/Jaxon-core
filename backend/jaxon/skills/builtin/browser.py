"""Authorized browser automation via Playwright (optional extra: pip install '.[browser]')."""

from __future__ import annotations

from pydantic import Field

from jaxon.core.errors import DependencyUnavailable
from jaxon.skills.base import Permission, SkillContext, SkillInput, SkillOutput, skill
from jaxon.skills.builtin._policy import check_url_allowed


class PageIn(SkillInput):
    url: str = Field(description="http(s) URL on an authorized domain")
    max_chars: int = Field(default=20_000, ge=100, le=200_000)


class PageOut(SkillOutput):
    url: str
    final_url: str
    title: str
    text: str
    truncated: bool


@skill(
    name="browser.page_text",
    description="Open a page in headless Chromium (runs JavaScript) and return the rendered "
    "title and visible text. Only ALLOWED_WEB_DOMAINS are permitted.",
    tags=("browser", "research"),
    permissions=(Permission.network, Permission.browser),
    timeout_seconds=60,
)
async def page_text(inp: PageIn, ctx: SkillContext) -> PageOut:
    allowed = ctx.settings.allowed_web_domains
    check_url_allowed(inp.url, allowed)
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise DependencyUnavailable(
            "Playwright is not installed (pip install '.[browser]')"
        ) from None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, executable_path=ctx.settings.browser_executable_path or None
        )
        try:
            page = await browser.new_page()

            # Block any request (redirects, sub-resources) to hosts outside the allowlist.
            async def guard(route):
                try:
                    check_url_allowed(route.request.url, allowed)
                except Exception:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", guard)
            await page.goto(inp.url, wait_until="domcontentloaded", timeout=30_000)
            check_url_allowed(page.url, allowed)
            title = await page.title()
            text = await page.inner_text("body")
            final_url = page.url
        finally:
            await browser.close()
    return PageOut(
        url=inp.url,
        final_url=final_url,
        title=title,
        text=text[: inp.max_chars],
        truncated=len(text) > inp.max_chars,
    )
