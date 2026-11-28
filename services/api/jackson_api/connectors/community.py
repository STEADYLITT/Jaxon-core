"""Discord and Telegram: member access from real Whop memberships, plus announcements
(ADR-044).

Discord (docs.discord.com/developers, 2026-11-27):
- base https://discord.com/api/v10, `Authorization: Bot <DISCORD_BOT_TOKEN>`,
  `User-Agent: DiscordBot (https://github.com/STEADYLITT/Jaxon-core, 1.0)`
- GET    /guilds/{guild}?with_counts=true                -> the server (connection test)
- PUT    /guilds/{guild}/members/{user}/roles/{role}     -> 204 (needs Manage Roles, and the
  bot's own role must sit ABOVE the member role)
- DELETE /guilds/{guild}/members/{user}/roles/{role}     -> 204
- POST   /channels/{channel}/messages {content <= 2000}  -> the message
- 429 -> {retry_after} (retried later by the worker)
Telegram (core.telegram.org/bots/api):
- https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/<method> -> {ok, result | description}
- getMe (test), getChat, sendMessage {chat_id, text}
- createChatInviteLink {chat_id, member_limit: 1, expire_date} -> one-person invite link
- auto-invite (ADR-049): a Bot API bot can't add a user to a group by itself, so Jackson
  uses a join-request link (createChatInviteLink creates_join_request=true) plus a webhook
  (setWebhook allowed_updates=["chat_join_request"], secret_token checked on every call).
  When someone taps the link, Telegram sends chat_join_request {chat, from, user_chat_id};
  Jackson approves it (approveChatJoinRequest) only if that Telegram id belongs to a Whop
  member with access, otherwise declines it (declineChatJoinRequest) and tells them why
  (sendMessage to user_chat_id, allowed while the request is open)
- banChatMember + unbanChatMember {only_if_banned: true} removes a member who can rejoin
  later if they pay again

Rules: roles come ONLY from Whop's real membership status (active, trialing, completed =
access; anything else = no access). Announcements are public posts: each one needs the
owner's approval (or a Launch that names the channel).
"""

import os
import time

import httpx

from ..worker_errors import NonRetryableError

DISCORD = "https://discord.com/api/v10"
TELEGRAM = "https://api.telegram.org"
UA = "DiscordBot (https://github.com/STEADYLITT/Jaxon-core, 1.0)"


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=20)


def discord_configured() -> bool:
    return all(os.environ.get(v) for v in ("DISCORD_BOT_TOKEN", "DISCORD_GUILD_ID",
                                           "DISCORD_MEMBER_ROLE_ID"))


def telegram_configured() -> bool:
    return all(os.environ.get(v) for v in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"))


def _discord_check(r: httpx.Response, what: str):
    if r.status_code == 429 or r.status_code >= 500:
        r.raise_for_status()
    if r.status_code >= 400:
        try:
            msg = r.json().get("message")
        except ValueError:
            msg = None
        raise NonRetryableError(f"Discord refused the {what}: {str(msg or r.status_code)[:300]}")
    return r.json() if r.content else {}


class Discord:
    def __init__(self, token: str, guild: str, client: httpx.Client | None = None):
        self._h = {"Authorization": f"Bot {token}", "User-Agent": UA}
        self.guild = guild
        self._client = client or _new_client()

    def server(self) -> dict:
        return _discord_check(self._client.get(f"{DISCORD}/guilds/{self.guild}",
                                               params={"with_counts": "true"}, headers=self._h),
                              "server lookup")

    def set_role(self, user_id: str, role_id: str, on: bool, reason: str) -> None:
        url = f"{DISCORD}/guilds/{self.guild}/members/{user_id}/roles/{role_id}"
        h = {**self._h, "X-Audit-Log-Reason": reason[:400]}
        r = self._client.put(url, headers=h) if on else self._client.delete(url, headers=h)
        if r.status_code == 404 and not on:
            return  # they already left the server: nothing to remove
        _discord_check(r, "role change")

    def post(self, channel_id: str, text: str) -> str:
        body = _discord_check(self._client.post(f"{DISCORD}/channels/{channel_id}/messages",
                                                headers=self._h, json={"content": text[:2000]}),
                              "announcement")
        return str(body.get("id") or "")


class Telegram:
    def __init__(self, token: str, chat_id: str, client: httpx.Client | None = None):
        self._base = f"{TELEGRAM}/bot{token}"
        self.chat = chat_id
        self._client = client or _new_client()

    def _call(self, method: str, **params) -> dict:
        r = self._client.post(f"{self._base}/{method}", json=params)
        if r.status_code == 429 or r.status_code >= 500:
            r.raise_for_status()
        try:
            body = r.json()
        except ValueError:
            body = {}
        if not body.get("ok"):
            raise NonRetryableError(f"Telegram refused {method}: "
                                    f"{str(body.get('description') or r.status_code)[:300]}")
        return body.get("result") or {}

    def me(self) -> dict:
        return self._call("getMe")

    def chat_info(self) -> dict:
        return self._call("getChat", chat_id=self.chat)

    def invite(self, name: str, hours: int = 48) -> str:
        res = self._call("createChatInviteLink", chat_id=self.chat, name=name[:32],
                         member_limit=1, expire_date=int(time.time()) + hours * 3600)
        return str(res.get("invite_link") or "")

    def join_request_link(self, name: str) -> str:
        """A reusable link whose joins wait for the bot's yes/no (auto-invite, ADR-049)."""
        res = self._call("createChatInviteLink", chat_id=self.chat, name=name[:32],
                         creates_join_request=True)
        return str(res.get("invite_link") or "")

    def set_webhook(self, url: str, secret: str) -> None:
        self._call("setWebhook", url=url, secret_token=secret,
                   allowed_updates=["chat_join_request"], drop_pending_updates=False)

    def approve(self, user_id: int) -> None:
        self._call("approveChatJoinRequest", chat_id=self.chat, user_id=int(user_id))

    def decline(self, user_id: int) -> None:
        self._call("declineChatJoinRequest", chat_id=self.chat, user_id=int(user_id))

    def tell(self, user_chat_id: int, text: str) -> None:
        """Message the person who asked to join (allowed while their request is open)."""
        self._call("sendMessage", chat_id=int(user_chat_id), text=text[:4096])

    def remove(self, user_id: str) -> None:
        self._call("banChatMember", chat_id=self.chat, user_id=int(user_id))
        self._call("unbanChatMember", chat_id=self.chat, user_id=int(user_id),
                   only_if_banned=True)

    def post(self, text: str) -> str:
        return str(self._call("sendMessage", chat_id=self.chat, text=text[:4096])
                   .get("message_id") or "")


def discord() -> Discord:
    if not discord_configured():
        raise NonRetryableError("Discord isn't connected: set DISCORD_BOT_TOKEN, "
                                "DISCORD_GUILD_ID and DISCORD_MEMBER_ROLE_ID in Railway")
    return Discord(os.environ["DISCORD_BOT_TOKEN"], os.environ["DISCORD_GUILD_ID"])


def telegram() -> Telegram:
    if not telegram_configured():
        raise NonRetryableError("Telegram isn't connected: set TELEGRAM_BOT_TOKEN and "
                                "TELEGRAM_CHAT_ID in Railway")
    return Telegram(os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"])
