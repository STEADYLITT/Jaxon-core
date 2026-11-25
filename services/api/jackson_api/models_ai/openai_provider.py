"""OpenAI, or any OpenAI-compatible local server (Ollama, vLLM, LM Studio), via the official
`openai` SDK's Chat Completions API.

Nothing is assumed: the provider is available only when its model name, and for cost
tracking its prices, are configured. No model name is hard-coded.
  OPENAI_API_KEY, OPENAI_MODEL, OPENAI_INPUT_USD_PER_MTOK, OPENAI_OUTPUT_USD_PER_MTOK
  LOCAL_MODEL_BASE_URL, LOCAL_MODEL_NAME            (local: cost 0)
Not yet verified against the live OpenAI API (no key provided); tested against the real
SDK with a mocked HTTP transport.
"""

import json
import os

import openai

from .base import ModelRefused, ModelRequest, ModelRequestInvalid, ModelResponse, cost_micros


class OpenAICompatibleProvider:
    def __init__(self, name: str, model: str | None, client: openai.OpenAI | None = None,
                 prices: tuple[float, float] | None = None, needs_key: bool = True,
                 base_url: str | None = None, max_retries: int = 1):
        self.name = name
        self.default_model = model
        self._client = client
        self._prices = prices
        self._needs_key = needs_key
        self._base_url = base_url
        self._max_retries = max_retries

    @classmethod
    def openai_from_env(cls) -> "OpenAICompatibleProvider":
        pin, pout = os.environ.get("OPENAI_INPUT_USD_PER_MTOK"), \
            os.environ.get("OPENAI_OUTPUT_USD_PER_MTOK")
        prices = (float(pin), float(pout)) if pin and pout else None
        return cls("openai", os.environ.get("OPENAI_MODEL"), prices=prices)

    @classmethod
    def local_from_env(cls) -> "OpenAICompatibleProvider":
        return cls("local", os.environ.get("LOCAL_MODEL_NAME"), prices=(0.0, 0.0),
                   needs_key=False, base_url=os.environ.get("LOCAL_MODEL_BASE_URL"))

    def available(self) -> bool:
        if not self.default_model:
            return False
        if self._client is not None:
            return True
        if self.name == "local":
            return bool(self._base_url)
        return bool(os.environ.get("OPENAI_API_KEY")) and self._prices is not None

    @property
    def client(self) -> openai.OpenAI:
        if self._client is None:
            kw = {"max_retries": self._max_retries}
            if self._base_url:
                kw["base_url"] = self._base_url
            if not self._needs_key:
                kw["api_key"] = os.environ.get("LOCAL_MODEL_API_KEY", "not-needed")
            self._client = openai.OpenAI(**kw)
        return self._client

    def complete(self, req: ModelRequest, model: str) -> ModelResponse:
        kwargs: dict = {
            "model": model,
            "messages": [{"role": "system", "content": req.system},
                         {"role": "user", "content": req.user}],
            "max_completion_tokens": req.max_tokens,
        }
        if req.json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": req.json_schema, "strict": True},
            }
        try:
            resp = self.client.chat.completions.create(**kwargs)
        except openai.RateLimitError:
            raise
        except openai.APIStatusError as e:
            if e.status_code >= 500:
                raise
            raise ModelRequestInvalid(f"{self.name} {e.status_code}: {e.message}") from None
        choice = resp.choices[0]
        msg = choice.message
        if getattr(msg, "refusal", None):
            raise ModelRefused(f"model declined the request: {msg.refusal}")
        text = msg.content or ""
        if req.json_schema is not None:
            json.loads(text)
        in_tok = resp.usage.prompt_tokens if resp.usage else 0
        out_tok = resp.usage.completion_tokens if resp.usage else 0
        prices = {resp.model: self._prices} if self._prices else {}
        cost = cost_micros(resp.model, in_tok, out_tok, prices)
        return ModelResponse(
            text=text, provider=self.name, model=resp.model, mode="live",
            input_tokens=in_tok, output_tokens=out_tok, cost_usd_micros=cost or 0,
            stop_reason=choice.finish_reason, request_id=getattr(resp, "_request_id", None),
            notes=[] if cost is not None else ["no price configured; cost recorded as 0"],
        )
