"""Claude via the official Anthropic Python SDK (patterns from the claude-api skill).

- Default model `claude-opus-5-5`. Thinking is always on for it, so we never send a
  `thinking` override and steer depth with `output_config.effort`. Its default effort is
  `medium`, so we always set effort explicitly.
- Server-side refusal fallbacks are on (`fallbacks: "default"` with the
  `server-side-fallback-2026-07-01` beta). We still check `stop_reason == "refusal"`,
  because the fallback model can decline too.
- Structured JSON via `output_config.format` (json_schema), no prefill (400 on this model).
- No `temperature` / `top_p` / `top_k` (400 on API version 2026-10-21).
- The SDK retries 429/5xx a little; the worker does durable retries on top of that.
"""

import json
import os
from urllib.parse import urlparse

import anthropic

from .base import (
    ModelRefused,
    ModelRequest,
    ModelRequestInvalid,
    ModelResponse,
    cost_micros,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


# Long thinking calls: allow 10 min to read, but give a slow network 20 s to connect
# (the SDK's default connect timeout is 5 s).
TIMEOUT = anthropic.Timeout(600.0, connect=20.0)  # the SDK's own (httpx2) type


class ModelUnreachable(Exception):
    """Retryable: the request never got an answer from Anthropic. The message names the
    real cause underneath the SDK's generic "Connection error." (DNS, TLS, proxy, reset,
    timeout), with any key scrubbed out."""


def clean_key(raw: str | None) -> str:
    """A key pasted into a dashboard often carries a trailing newline, spaces or quotes.
    Any of those makes the HTTP header illegal, which the SDK reports only as
    "Connection error.". Strip them."""
    return (raw or "").strip().strip("\"'").strip()


def base_url() -> str | None:
    """ANTHROPIC_BASE_URL only if it is a real https URL; otherwise the SDK default."""
    raw = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip().strip("\"'")
    if not raw:
        return None
    u = urlparse(raw)
    return raw.rstrip("/") if u.scheme == "https" and u.netloc else None


def config_report() -> dict:
    """What the Claude connection is set to, without any secret value (for the Test button
    and the worker's startup log)."""
    raw = os.environ.get("ANTHROPIC_API_KEY") or ""
    raw_base = (os.environ.get("ANTHROPIC_BASE_URL") or "").strip()
    return {
        "key_set": bool(raw),
        "key_had_stray_whitespace_or_quotes": bool(raw) and clean_key(raw) != raw,
        "key_looks_like_anthropic": clean_key(raw).startswith("sk-ant-"),
        "base_url_host": urlparse(base_url()).netloc if base_url() else "api.anthropic.com",
        "base_url_ignored_invalid": bool(raw_base) and base_url() is None,
        "auth_token_also_set": bool(os.environ.get("ANTHROPIC_AUTH_TOKEN")),
        "proxy_vars_set": sorted(k for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY",
                                             "http_proxy", "ALL_PROXY", "all_proxy")
                                 if os.environ.get(k)),
    }


def make_client(max_retries: int = 3, http_client=None) -> anthropic.Anthropic:
    extra = {"http_client": http_client} if http_client is not None else {}
    return anthropic.Anthropic(**extra, api_key=clean_key(os.environ.get("ANTHROPIC_API_KEY")),
                               # explicit, or the SDK would fall back to a bad env value
                               base_url=base_url() or "https://api.anthropic.com",
                               timeout=TIMEOUT, max_retries=max_retries)


def _scrub(text: str) -> str:
    out = str(text)
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        raw = os.environ.get(name) or ""
        for v in {raw, clean_key(raw), raw.strip(), repr(raw)[1:-1]}:
            if v and len(v) >= 6:
                out = out.replace(v, "•••")
    return out


def cause_chain(e: BaseException) -> str:
    """The SDK says only "Connection error."; the useful part is the exception under it."""
    parts, seen, cur = [], set(), e.__cause__ or e.__context__
    while cur is not None and id(cur) not in seen and len(parts) < 4:
        seen.add(id(cur))
        parts.append(f"{type(cur).__name__}: {cur}")
        cur = cur.__cause__ or cur.__context__
    return _scrub(" <- ".join(parts))[:600]


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: anthropic.Anthropic | None = None, max_retries: int = 3):
        self._client = client
        self._max_retries = max_retries

    def available(self) -> bool:
        return self._client is not None or bool(clean_key(os.environ.get("ANTHROPIC_API_KEY")))

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = make_client(self._max_retries)
        return self._client

    def complete(self, req: ModelRequest, model: str) -> ModelResponse:
        output_config: dict = {"effort": req.effort}
        if req.json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": req.json_schema}
        try:
            resp = self.client.beta.messages.create(
                model=model,
                max_tokens=req.max_tokens,
                system=req.system,
                messages=[{"role": "user", "content": req.user}],
                output_config=output_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except anthropic.RateLimitError:
            raise  # retryable: the worker backs off and retries
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                raise
            raise ModelRequestInvalid(f"anthropic {e.status_code}: {_scrub(e.message)}") from None
        except anthropic.APIConnectionError as e:  # includes APITimeoutError
            cause = cause_chain(e)
            if "Illegal header value" in cause or "LocalProtocolError" in cause:
                raise ModelRequestInvalid(
                    "ANTHROPIC_API_KEY can't be sent as a header (a stray space, newline or "
                    "quote in it?). Re-paste the key in Railway.") from None
            kind = "timed out" if isinstance(e, anthropic.APITimeoutError) else "no connection"
            raise ModelUnreachable(f"Anthropic {kind} after {self._max_retries} retries"
                                   f"{f' ({cause})' if cause else ''}") from None

        served = resp.model
        in_tok, out_tok = resp.usage.input_tokens, resp.usage.output_tokens
        cost = cost_micros(served, in_tok, out_tok)
        notes = [] if cost is not None else [f"no price on file for {served}; cost recorded as 0"]
        if resp.stop_reason == "refusal":
            category = getattr(resp.stop_details, "category", None) if resp.stop_details else None
            raise ModelRefused(f"model declined the request (category: {category})")
        text = "".join(b.text for b in resp.content if b.type == "text")
        if req.json_schema is not None:
            json.loads(text)  # output_config.format guarantees JSON; fail loudly if not
        return ModelResponse(
            text=text, provider=self.name, model=served, mode="live",
            input_tokens=in_tok, output_tokens=out_tok, cost_usd_micros=cost or 0,
            stop_reason=resp.stop_reason, request_id=getattr(resp, "_request_id", None),
            notes=notes,
        )
