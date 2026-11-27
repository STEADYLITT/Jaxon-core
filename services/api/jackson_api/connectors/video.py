"""Short UGC/product video clips via fal.ai (ADR-036).

Checked against fal.ai/models/fal-ai/veo3.1/fast/api (2026-11-27):
- queue: POST https://queue.fal.run/<model>  (Authorization: Key <FAL_KEY>, JSON input)
  -> {request_id, status_url, response_url}; GET status_url -> {"status": IN_QUEUE |
  IN_PROGRESS | COMPLETED}; GET response_url -> {"video": {"url": ...}}
- input: prompt, aspect_ratio ("16:9" | "9:16"), duration ("4s" | "6s" | "8s"),
  resolution ("720p" | "1080p" | "4k"), generate_audio (bool)
- price (veo3.1 fast): $0.10/s without audio, $0.15/s with audio at 720p/1080p;
  $0.30/s and $0.35/s at 4k

Settings (names only): FAL_KEY; optional FAL_VIDEO_MODEL (another fal model then also
needs FAL_VIDEO_CENTS_PER_SECOND, since we only know veo3.1 fast's price). Without FAL_KEY
generation is a labeled MOCK that produces no real video. Every generation is a SPEND that
needs the owner's approval of the shown estimate, or a Launch approval with budget left.
"""

import os
import time
import uuid

import httpx

from ..worker_errors import NonRetryableError

QUEUE = "https://queue.fal.run"
DEFAULT_MODEL = "fal-ai/veo3.1/fast"
DURATIONS = (4, 6, 8)
RESOLUTIONS = ("720p", "1080p", "4k")
ASPECTS = ("9:16", "16:9")
# cents per second for DEFAULT_MODEL: (is_4k, audio) -> cents
PRICE = {(False, False): 10, (False, True): 15, (True, False): 30, (True, True): 35}


class VideoPending(Exception):
    """fal hasn't finished yet: the worker retries later and resumes the same request."""


def model() -> str:
    return os.environ.get("FAL_VIDEO_MODEL") or DEFAULT_MODEL


def validate(duration_s: int, resolution: str, aspect_ratio: str) -> None:
    if duration_s not in DURATIONS:
        raise NonRetryableError(f"duration must be one of {DURATIONS} seconds")
    if resolution not in RESOLUTIONS:
        raise NonRetryableError(f"resolution must be one of {RESOLUTIONS}")
    if aspect_ratio not in ASPECTS:
        raise NonRetryableError(f"aspect ratio must be one of {ASPECTS}")


def estimate_cents(duration_s: int, resolution: str, audio: bool) -> int:
    """What fal charges for one clip, shown to the owner before anything is generated."""
    validate(duration_s, resolution, "9:16")
    custom = os.environ.get("FAL_VIDEO_CENTS_PER_SECOND")
    if custom:
        return int(custom) * duration_s
    if model() != DEFAULT_MODEL:
        raise NonRetryableError(
            f"set FAL_VIDEO_CENTS_PER_SECOND: the price of {model()} isn't known to Jackson")
    return PRICE[(resolution == "4k", bool(audio))] * duration_s


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=60)


def _check(r: httpx.Response, what: str) -> dict:
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()  # retryable
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        detail = body.get("detail") or body.get("error") or r.status_code
        raise NonRetryableError(f"fal.ai refused the {what}: {str(detail)[:300]}")
    return body


class FalVideo:
    backend = "fal"

    def __init__(self, key: str, model_id: str, client: httpx.Client | None = None,
                 sleep=time.sleep):
        self._headers = {"Authorization": f"Key {key}", "Content-Type": "application/json"}
        self.model = model_id
        self._client = client or _new_client()
        self._sleep = sleep

    def submit(self, prompt: str, duration_s: int, aspect_ratio: str, resolution: str,
               audio: bool) -> dict:
        validate(duration_s, resolution, aspect_ratio)
        body = _check(self._client.post(f"{QUEUE}/{self.model}", headers=self._headers, json={
            "prompt": prompt[:4000], "duration": f"{duration_s}s", "aspect_ratio": aspect_ratio,
            "resolution": resolution, "generate_audio": bool(audio)}), "video request")
        if not body.get("request_id"):
            raise NonRetryableError("fal.ai returned no request id")
        base = f"{QUEUE}/{self.model.split('/')[0]}/{self.model.split('/')[1]}" \
               f"/requests/{body['request_id']}"
        return {"request_id": body["request_id"],
                "status_url": body.get("status_url") or f"{base}/status",
                "response_url": body.get("response_url") or base}

    def wait(self, job: dict, timeout_s: float = 600, poll_s: float = 5) -> str:
        """Poll until the clip is ready; return its public video URL."""
        deadline = time.monotonic() + timeout_s
        while True:
            st = _check(self._client.get(job["status_url"], headers=self._headers), "status")
            status = st.get("status")
            if status == "COMPLETED":
                break
            if status not in ("IN_QUEUE", "IN_PROGRESS"):
                raise NonRetryableError(f"fal.ai video failed: {str(st)[:300]}")
            if time.monotonic() >= deadline:
                raise VideoPending(f"fal.ai request {job['request_id']} still {status}")
            self._sleep(poll_s)
        out = _check(self._client.get(job["response_url"], headers=self._headers), "result")
        url = ((out.get("video") or {}).get("url")) or ""
        if not url.startswith("https://"):
            raise NonRetryableError("fal.ai finished without a video URL")
        return url


class MockVideo:
    """MOCK: nothing is generated or paid for; the asset is marked as a stand-in."""

    backend = "mock"
    model = "mock"

    def submit(self, prompt, duration_s, aspect_ratio, resolution, audio) -> dict:
        validate(duration_s, resolution, aspect_ratio)
        rid = f"mock-{uuid.uuid4().hex[:10]}"
        return {"request_id": rid, "status_url": "", "response_url": ""}

    def wait(self, job, timeout_s=600, poll_s=5) -> str:
        return f"mock://video/{job['request_id']}.mp4"  # not https: never posted


def configured() -> bool:
    return bool(os.environ.get("FAL_KEY"))


def video_backend():
    if not configured():
        return MockVideo()
    return FalVideo(os.environ["FAL_KEY"], model())
