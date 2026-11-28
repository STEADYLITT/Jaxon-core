"""Platform follower analytics (ADR-058): the follower count each platform itself reports,
read once a day. Growth is the difference between two real readings, never a projection.

PLATFORM_ANALYTICS lists the platforms to read, comma separated (youtube,instagram,tiktok):
- youtube    GET https://www.googleapis.com/youtube/v3/channels?part=snippet,statistics
             &id=<YOUTUBE_CHANNEL_IDS>&key=<YOUTUBE_API_KEY>  -> statistics.subscriberCount
             (1 quota unit per call; a channel that hides its count is skipped)
- instagram  GET https://graph.facebook.com/v21.0/<id>?fields=username,followers_count
             &access_token=<META_ACCESS_TOKEN> for each of INSTAGRAM_USER_IDS
             (Instagram professional accounts linked to a Facebook Page)
- tiktok     GET https://open.tiktokapis.com/v2/user/info/?fields=open_id,username,
             follower_count  with  Authorization: Bearer <TIKTOK_DISPLAY_TOKEN>
             (a user access token with the user.info.stats scope)
"""

import os

import httpx

from ..worker_errors import NonRetryableError

YT = "https://www.googleapis.com/youtube/v3/channels"
IG = "https://graph.facebook.com/v21.0"
TT = "https://open.tiktokapis.com/v2/user/info/"
PLATFORMS = ("youtube", "instagram", "tiktok")
NEEDS = {"youtube": ("YOUTUBE_API_KEY", "YOUTUBE_CHANNEL_IDS"),
         "instagram": ("META_ACCESS_TOKEN", "INSTAGRAM_USER_IDS"),
         "tiktok": ("TIKTOK_DISPLAY_TOKEN",)}


def _list(var: str) -> list[str]:
    return [x.strip() for x in (os.environ.get(var) or "").split(",") if x.strip()]


def enabled() -> list[str]:
    return [p for p in _list("PLATFORM_ANALYTICS") if p.lower() in PLATFORMS]


def missing() -> dict[str, list[str]]:
    """For each enabled platform, the variables it still needs."""
    return {p: [v for v in NEEDS[p] if not os.environ.get(v)] for p in enabled()}


def configured() -> bool:
    on = enabled()
    return bool(on) and not any(missing().values())


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=30)


def _check(r: httpx.Response, who: str) -> dict:
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()  # retryable
    try:
        body = r.json()
    except ValueError:
        body = {}
    if r.status_code >= 400:
        err = body.get("error") if isinstance(body, dict) else None
        msg = err.get("message") if isinstance(err, dict) else err
        raise NonRetryableError(f"{who} refused the request: {str(msg or r.status_code)[:200]}")
    return body if isinstance(body, dict) else {}


def youtube(c: httpx.Client) -> list[dict]:
    ids = _list("YOUTUBE_CHANNEL_IDS")[:50]
    body = _check(c.get(YT, params={"part": "snippet,statistics", "id": ",".join(ids),
                                    "key": os.environ["YOUTUBE_API_KEY"]}), "YouTube")
    out = []
    for it in body.get("items") or []:
        st = it.get("statistics") or {}
        if st.get("hiddenSubscriberCount") or st.get("subscriberCount") is None:
            continue  # hidden: never guessed
        out.append({"platform": "youtube", "account_id": str(it.get("id"))[:64],
                    "handle": str((it.get("snippet") or {}).get("customUrl")
                                  or (it.get("snippet") or {}).get("title") or "")[:120],
                    "followers": int(st["subscriberCount"])})
    return out


def instagram(c: httpx.Client) -> list[dict]:
    out = []
    for uid in _list("INSTAGRAM_USER_IDS")[:20]:
        body = _check(c.get(f"{IG}/{uid}", params={
            "fields": "username,followers_count",
            "access_token": os.environ["META_ACCESS_TOKEN"]}), "Instagram")
        if body.get("followers_count") is None:
            continue  # not a professional account / no permission: never guessed
        out.append({"platform": "instagram", "account_id": str(body.get("id") or uid)[:64],
                    "handle": str(body.get("username") or "")[:120],
                    "followers": int(body["followers_count"])})
    return out


def tiktok(c: httpx.Client) -> list[dict]:
    body = _check(c.get(TT, params={"fields": "open_id,username,follower_count"},
                        headers={"Authorization": f"Bearer {os.environ['TIKTOK_DISPLAY_TOKEN']}"}),
                  "TikTok")
    err = body.get("error") or {}
    if isinstance(err, dict) and err.get("code") not in (None, "ok"):
        raise NonRetryableError(f"TikTok refused the request: {err.get('message') or err['code']}")
    u = (body.get("data") or {}).get("user") or {}
    if u.get("follower_count") is None:
        return []
    return [{"platform": "tiktok", "account_id": str(u.get("open_id") or "me")[:64],
             "handle": str(u.get("username") or "")[:120],
             "followers": int(u["follower_count"])}]


READERS = {"youtube": youtube, "instagram": instagram, "tiktok": tiktok}


def read_all() -> tuple[list[dict], dict[str, str]]:
    """(readings, errors by platform). One platform failing never hides the others."""
    if not enabled():
        raise NonRetryableError("set PLATFORM_ANALYTICS (e.g. youtube,instagram,tiktok)")
    readings, errors = [], {}
    gaps = missing()
    with _new_client() as c:
        for p in enabled():
            if gaps[p]:
                errors[p] = "set " + ", ".join(gaps[p])
                continue
            try:
                readings += READERS[p](c)
            except NonRetryableError as e:
                errors[p] = str(e)
    return readings, errors
