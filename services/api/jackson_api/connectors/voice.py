"""AI voice via ElevenLabs: course narration and ad voiceovers (ADR-042).

Checked against elevenlabs.io/docs (2026-11-27):
- POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128
  header `xi-api-key`, JSON {text, model_id, voice_settings} -> the audio bytes (mp3);
  errors are 4xx JSON {detail}
- GET /v1/user/subscription -> tier, character_count, character_limit,
  next_character_count_reset_unix (the connection test: free)
- GET /v2/voices?page_size=&search= -> {voices: [{voice_id, name, category, labels}]}
- per-request character limits: eleven_multilingual_v2 10,000; eleven_flash_v2_5 40,000
- price on the API (pay as you go): multilingual v2 about $0.08 per 1,000 characters,
  Flash v2.5 half that. ELEVENLABS_CENTS_PER_1K_CHARS overrides the estimate.

Rules: no voice cloning of a real person without their consent (ElevenLabs prohibits it,
and Jackson only uses voices from the account's own voice library); every generated
clip is stored labeled "AI voice". Each generation is a SPEND approved first (or covered
by an active Launch with budget left). Without ELEVENLABS_API_KEY it's a labeled MOCK that
produces no audio.
"""

import math
import os

import httpx

from ..worker_errors import NonRetryableError

API = "https://api.elevenlabs.io"
DEFAULT_MODEL = "eleven_multilingual_v2"
MODELS = {"eleven_multilingual_v2": (10_000, 8.0), "eleven_flash_v2_5": (40_000, 4.0)}
OUTPUT = "mp3_44100_128"
MAX_CHARS = 30_000  # one narration task: split longer scripts into lessons


def configured() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY"))


def model() -> str:
    m = os.environ.get("ELEVENLABS_MODEL") or DEFAULT_MODEL
    if m not in MODELS:
        raise NonRetryableError(f"ELEVENLABS_MODEL must be one of {sorted(MODELS)}")
    return m


def estimate_cents(chars: int, model_id: str | None = None) -> int:
    custom = os.environ.get("ELEVENLABS_CENTS_PER_1K_CHARS")
    rate = float(custom) if custom else MODELS[model_id or model()][1]
    return max(1, math.ceil(chars * rate / 1000))


def chunks(text: str, limit: int) -> list[str]:
    """Split on paragraph/sentence boundaries under the model's per-request limit."""
    text = (text or "").strip()
    if len(text) <= limit:
        return [text] if text else []  # sent exactly as written
    out, cur = [], ""
    for para in [p.strip() for p in text.split("\n") if p.strip()]:
        pieces = [para] if len(para) <= limit else [
            s.strip() + "." for s in para.split(". ") if s.strip()]
        for piece in pieces:
            while len(piece) > limit:  # one huge sentence: hard cut
                out.append(piece[:limit])
                piece = piece[limit:]
            if len(cur) + len(piece) + 1 > limit and cur:
                out.append(cur)
                cur = piece
            else:
                cur = f"{cur}\n{piece}" if cur else piece
    if cur:
        out.append(cur)
    return out


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=120)


def _check(r: httpx.Response, what: str) -> httpx.Response:
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()  # retryable
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail")
        except ValueError:
            detail = r.status_code
        if isinstance(detail, dict):
            detail = detail.get("message") or detail
        raise NonRetryableError(f"ElevenLabs refused the {what}: {str(detail)[:300]}")
    return r


class ElevenLabs:
    backend = "elevenlabs"

    def __init__(self, key: str, client: httpx.Client | None = None):
        self._h = {"xi-api-key": key}
        self._client = client or _new_client()
        self.model = model()

    def subscription(self) -> dict:
        return _check(self._client.get(f"{API}/v1/user/subscription", headers=self._h),
                      "key").json()

    def voices(self, search: str = "") -> list[dict]:
        params = {"page_size": 100}
        if search:
            params["search"] = search
        body = _check(self._client.get(f"{API}/v2/voices", params=params, headers=self._h),
                      "voice list").json()
        return [{"voice_id": v.get("voice_id"), "name": v.get("name"),
                 "category": v.get("category")} for v in body.get("voices") or []]

    def speak(self, voice_id: str, text: str) -> bytes:
        limit = MODELS[self.model][0]
        audio = b""
        for part in chunks(text, limit):
            r = _check(self._client.post(
                f"{API}/v1/text-to-speech/{voice_id}", params={"output_format": OUTPUT},
                headers={**self._h, "Accept": "audio/mpeg"},
                json={"text": part, "model_id": self.model}), "narration")
            if not r.content:
                raise NonRetryableError("ElevenLabs returned no audio")
            audio += r.content  # mp3 frames concatenate cleanly
        return audio


class MockVoice:
    """MOCK: nothing is generated or paid for; the clip is marked as a stand-in."""

    backend = "mock"
    model = "mock"

    def voices(self, search=""):
        return []

    def speak(self, voice_id: str, text: str) -> bytes:
        return b""  # no audio: the asset says MOCK and is never used anywhere


def voice_backend():
    return ElevenLabs(os.environ["ELEVENLABS_API_KEY"]) if configured() else MockVoice()
