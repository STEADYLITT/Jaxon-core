"""Social posting connector (phase 16 stand-in, made live in ADR-034): Upload-Post.

Checked against docs.upload-post.com (2026-11-27):
- POST https://api.upload-post.com/api/upload_text    text posts (x, facebook, linkedin, ...)
- POST https://api.upload-post.com/api/upload_photos  photos[] = public image URLs
- POST https://api.upload-post.com/api/upload         video = public video URL
- headers: Authorization: Apikey <key>; Idempotency-Key (a retried request returns the
  existing job instead of posting twice)
- form fields: user (the Upload-Post profile), platform[], title (the caption);
  is_ai_generated=true asks Instagram/TikTok to label AI-made media
- response: {"success": true, "results": {"<platform>": {"success", "url", "post_id"}}}

Settings (names only): UPLOADPOST_API_KEY, UPLOADPOST_USER. Without both, posting is a
clearly labeled MOCK. Either way, a post only happens after the content was approved by
the owner or is inside an active Launch approval (enforced in content.publish_handler,
the policy engine and a DB trigger).
"""

import os
import uuid

import httpx

from ..worker_errors import NonRetryableError

API = "https://api.upload-post.com/api"
PLATFORM = {"instagram": "instagram", "tiktok": "tiktok", "youtube_shorts": "youtube",
            "x": "x", "facebook": "facebook"}
NEEDS_MEDIA = {"instagram", "tiktok", "youtube"}
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm")


def _media(assets: list[dict]) -> tuple[list[str], list[str]]:
    """Public https URLs of cleared assets, split into (videos, images)."""
    videos, images = [], []
    for a in assets or []:
        url = a.get("url") or ""
        if not url.startswith("https://"):
            continue
        is_video = a.get("kind") == "video" or url.lower().split("?")[0].endswith(VIDEO_EXT)
        (videos if is_video else images).append(url)
    return videos, images


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=120)


class UploadPost:
    backend = "upload-post"

    def __init__(self, api_key: str, user: str, client: httpx.Client | None = None):
        self._headers = {"Authorization": f"Apikey {api_key}", "Accept": "application/json"}
        self.user = user
        self._client = client or _new_client()

    def publish(self, channel: str, text: str, assets: list[dict], idempotency_key: str,
                ai_generated: bool) -> dict:
        platform = PLATFORM.get(channel)
        if platform is None:
            raise NonRetryableError(f"'{channel}' isn't a social channel Upload-Post posts to")
        videos, images = _media(assets)
        fields: dict = {"user": self.user, "platform[]": platform, "title": text[:2200]}
        if videos:
            endpoint = "upload"
            fields["video"] = videos[0]
        elif images:
            endpoint = "upload_photos"
            fields["photos[]"] = images[:10]  # repeated form field, one per URL
        else:
            if platform in NEEDS_MEDIA:
                raise NonRetryableError(f"{platform} needs an image or a video to post")
            endpoint = "upload_text"
        if ai_generated and endpoint != "upload_text":
            fields["is_ai_generated"] = "true"
        r = self._client.post(f"{API}/{endpoint}", data=fields,
                              headers={**self._headers, "Idempotency-Key": idempotency_key})
        if r.status_code == 429 or r.status_code >= 500:
            r.raise_for_status()  # retryable; the Idempotency-Key prevents a double post
        try:
            body = r.json()
        except ValueError:
            raise NonRetryableError(f"Upload-Post HTTP {r.status_code}") from None
        res = (body.get("results") or {}).get(platform) or {}
        if r.status_code >= 400 or not body.get("success") or res.get("success") is False:
            msg = res.get("error") or body.get("message") or body.get("error") or r.status_code
            raise NonRetryableError(f"Upload-Post refused the post: {str(msg)[:300]}")
        return {"post_id": str(res.get("post_id") or res.get("url") or body.get("request_id")
                               or ""), "url": res.get("url"), "platform": platform,
                "connector": self.backend}


class MockSocial:
    backend = "mock"

    def publish(self, channel, text, assets, idempotency_key, ai_generated) -> dict:
        return {"post_id": f"mock-post-{uuid.uuid4().hex[:10]}", "url": None,
                "platform": PLATFORM.get(channel, channel), "connector": "mock"}


def configured() -> bool:
    return bool(os.environ.get("UPLOADPOST_API_KEY") and os.environ.get("UPLOADPOST_USER"))


def social_backend():
    if not configured():
        return MockSocial()
    return UploadPost(os.environ["UPLOADPOST_API_KEY"], os.environ["UPLOADPOST_USER"])
