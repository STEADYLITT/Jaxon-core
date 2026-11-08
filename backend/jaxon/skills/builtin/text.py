from __future__ import annotations

import re

from pydantic import Field

from jaxon.skills.base import SkillContext, SkillInput, SkillOutput, skill

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL = re.compile(r"https?://[^\s<>\"')\]]+")
_PHONE = re.compile(r"(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]?\d{3,4}[\s.-]?\d{3,4}")


def _uniq(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


class ContactsIn(SkillInput):
    text: str = Field(description="Raw text (web page, email, notes)", max_length=500_000)


class ContactsOut(SkillOutput):
    emails: list[str]
    urls: list[str]
    phones: list[str]


@skill(
    name="text.extract_contacts",
    description="Extract unique email addresses, URLs and phone numbers from text. "
    "Useful for lead research on pages you are authorized to read.",
    tags=("text", "leads"),
)
async def extract_contacts(inp: ContactsIn, ctx: SkillContext) -> ContactsOut:
    emails = _uniq([e.lower().rstrip(".") for e in _EMAIL.findall(inp.text)])
    urls = _uniq([u.rstrip(".,;:") for u in _URL.findall(inp.text)])
    phones = _uniq(
        [p.strip() for p in _PHONE.findall(inp.text) if sum(ch.isdigit() for ch in p) >= 7]
    )
    return ContactsOut(emails=emails, urls=urls, phones=phones)
