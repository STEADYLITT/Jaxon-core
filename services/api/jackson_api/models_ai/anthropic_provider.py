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

import anthropic

from .base import (
    ModelRefused,
    ModelRequest,
    ModelRequestInvalid,
    ModelResponse,
    cost_micros,
)

FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, client: anthropic.Anthropic | None = None, max_retries: int = 1):
        self._client = client
        self._max_retries = max_retries

    def available(self) -> bool:
        return self._client is not None or bool(os.environ.get("ANTHROPIC_API_KEY"))

    @property
    def client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(max_retries=self._max_retries)
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
            raise ModelRequestInvalid(f"anthropic {e.status_code}: {e.message}") from None
        # APIConnectionError / APITimeoutError propagate as retryable.

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
