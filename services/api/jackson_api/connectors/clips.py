"""Clip discovery on YouTube and Twitch (ADR-052): find clippable moments from creators
whose terms allow clipping, with the source link and the numbers behind each pick.

YouTube Data API v3 (developers.google.com/youtube/v3):
- key auth: ?key=YOUTUBE_API_KEY
- GET /youtube/v3/search?part=snippet&type=video&q=&channelId=&order=viewCount
      &publishedAfter=RFC3339&maxResults<=50   (100 quota units per call)
- GET /youtube/v3/videos?part=snippet,statistics,contentDetails,status&id=a,b,c (1 unit)
  -> statistics.viewCount/likeCount/commentCount, contentDetails.duration (ISO 8601),
     status.license ("creativeCommon" or "youtube")
Twitch Helix (dev.twitch.tv/docs/api):
- app token: POST https://id.twitch.tv/oauth2/token client_id, client_secret,
  grant_type=client_credentials -> {access_token, expires_in}
- headers Client-Id + Authorization: Bearer <app token>
- GET /helix/users?login=a&login=b -> data[{id, login, display_name}]
- GET /helix/clips?broadcaster_id=&started_at=RFC3339&first<=100 -> data[{id, url, title,
  view_count, created_at, duration, creator_name, broadcaster_name, vod_offset}]
- GET /helix/games/top?first=1 (connection test)

Rights: a pick is "allowed" only when the creator is on CLIP_ALLOWED_CREATORS (Steady's list
of creators whose terms or written permission allow clipping) or, on YouTube, the video is
Creative Commons (attribution required). Everything else is "ask the creator first".
Discovery is read-only and free (API quota only); posting still needs approval or a Launch.
"""

import os
import re
import time
from datetime import UTC, datetime, timedelta

import httpx

from ..worker_errors import NonRetryableError

YT = "https://www.googleapis.com/youtube/v3"
TW_TOKEN = "https://id.twitch.tv/oauth2/token"
TW = "https://api.twitch.tv/helix"


def youtube_configured() -> bool:
    return bool(os.environ.get("YOUTUBE_API_KEY"))


def twitch_configured() -> bool:
    return bool(os.environ.get("TWITCH_CLIENT_ID") and os.environ.get("TWITCH_CLIENT_SECRET"))


def allowed_creators() -> set[str]:
    return {x.strip().lower() for x in (os.environ.get("CLIP_ALLOWED_CREATORS") or "")
            .split(",") if x.strip()}


def seconds(iso: str) -> int:
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=20)


def _check(r: httpx.Response, who: str, what: str) -> dict:
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        err = body.get("error") if isinstance(body, dict) else None
        msg = err.get("message") if isinstance(err, dict) else body.get("message")
        raise NonRetryableError(f"{who} refused the {what}: {str(msg or r.status_code)[:300]}")
    return body


def _since(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=max(1, min(days, 60)))).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def rights(creator: str, license_: str | None = None) -> str:
    if creator.lower() in allowed_creators():
        return "allowed"
    if license_ == "creativeCommon":
        return "allowed (Creative Commons: credit the creator)"
    return "ask the creator first"


class YouTube:
    def __init__(self, key: str, client: httpx.Client | None = None):
        self._key = key
        self._client = client or _new_client()

    def ping(self) -> dict:
        b = _check(self._client.get(f"{YT}/videos", params={
            "part": "id", "id": "dQw4w9WgXcQ", "key": self._key}), "YouTube", "key")
        return {"ok": True, "items": len(b.get("items") or [])}

    def find(self, query: str = "", channel_id: str = "", days: int = 7,
             limit: int = 20) -> list[dict]:
        params = {"part": "snippet", "type": "video", "order": "viewCount", "key": self._key,
                  "publishedAfter": _since(days), "maxResults": max(1, min(limit, 50))}
        if query:
            params["q"] = query[:200]
        if channel_id:
            params["channelId"] = channel_id
        hits = _check(self._client.get(f"{YT}/search", params=params), "YouTube", "search")
        ids = [(i.get("id") or {}).get("videoId") for i in hits.get("items") or []]
        ids = [i for i in ids if i]
        if not ids:
            return []
        vids = _check(self._client.get(f"{YT}/videos", params={
            "part": "snippet,statistics,contentDetails,status", "id": ",".join(ids),
            "key": self._key}), "YouTube", "video details")
        out = []
        for v in vids.get("items") or []:
            sn, st = v.get("snippet") or {}, v.get("statistics") or {}
            views = int(st.get("viewCount") or 0)
            try:
                pub = datetime.fromisoformat(str(sn.get("publishedAt")).replace("Z", "+00:00"))
            except ValueError:
                pub = datetime.now(UTC)
            age_h = max(1.0, (datetime.now(UTC) - pub).total_seconds() / 3600)
            lic = (v.get("status") or {}).get("license")
            out.append({"platform": "youtube", "id": v.get("id"),
                        "url": f"https://www.youtube.com/watch?v={v.get('id')}",
                        "title": sn.get("title"), "creator": sn.get("channelTitle"),
                        "published_at": sn.get("publishedAt"), "views": views,
                        "likes": int(st.get("likeCount") or 0),
                        "comments": int(st.get("commentCount") or 0),
                        "duration_s": seconds((v.get("contentDetails") or {}).get("duration")),
                        "views_per_hour": round(views / age_h, 1), "license": lic,
                        "rights": rights(str(sn.get("channelTitle") or ""), lic)})
        return out


class Twitch:
    def __init__(self, client_id: str, secret: str, client: httpx.Client | None = None):
        self._id, self._secret = client_id, secret
        self._client = client or _new_client()
        self._token, self._exp = None, 0.0

    def _h(self) -> dict:
        if not self._token or time.time() > self._exp - 60:
            b = _check(self._client.post(TW_TOKEN, data={
                "client_id": self._id, "client_secret": self._secret,
                "grant_type": "client_credentials"}), "Twitch", "app login")
            self._token = b.get("access_token")
            self._exp = time.time() + int(b.get("expires_in") or 3600)
            if not self._token:
                raise NonRetryableError("Twitch returned no app token")
        return {"Client-Id": self._id, "Authorization": f"Bearer {self._token}"}

    def ping(self) -> dict:
        b = _check(self._client.get(f"{TW}/games/top", params={"first": 1}, headers=self._h()),
                   "Twitch", "test call")
        top = (b.get("data") or [{}])[0]
        return {"ok": True, "top_game": top.get("name")}

    def find(self, logins: list[str], days: int = 7, limit: int = 20) -> list[dict]:
        logins = [x.strip().lower() for x in logins if x.strip()][:20]
        if not logins:
            raise NonRetryableError("name the Twitch channels to scout (logins)")
        users = _check(self._client.get(f"{TW}/users", params=[("login", x) for x in logins],
                                        headers=self._h()), "Twitch", "channel lookup")
        out = []
        for u in users.get("data") or []:
            clips = _check(self._client.get(f"{TW}/clips", headers=self._h(), params={
                "broadcaster_id": u["id"], "started_at": _since(days),
                "first": max(1, min(limit, 100))}), "Twitch", "clips")
            for c in clips.get("data") or []:
                out.append({"platform": "twitch", "id": c.get("id"), "url": c.get("url"),
                            "title": c.get("title"), "creator": u.get("display_name"),
                            "published_at": c.get("created_at"),
                            "views": int(c.get("view_count") or 0),
                            "duration_s": round(float(c.get("duration") or 0)),
                            "clipped_by": c.get("creator_name"),
                            "vod_offset_s": c.get("vod_offset"),
                            "rights": rights(str(u.get("login") or ""))})
        return out


def youtube() -> YouTube:
    if not youtube_configured():
        raise NonRetryableError("set YOUTUBE_API_KEY in Railway")
    return YouTube(os.environ["YOUTUBE_API_KEY"])


def twitch() -> Twitch:
    if not twitch_configured():
        raise NonRetryableError("set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET in Railway")
    return Twitch(os.environ["TWITCH_CLIENT_ID"], os.environ["TWITCH_CLIENT_SECRET"])
