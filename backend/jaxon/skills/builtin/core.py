from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

from jaxon.core.errors import ValidationFailed
from jaxon.skills.base import SkillContext, SkillInput, SkillOutput, skill


class EchoIn(SkillInput):
    text: str = Field(description="Text to return unchanged", max_length=20_000)


class EchoOut(SkillOutput):
    text: str


@skill(
    name="core.echo",
    description="Return the given text unchanged. Diagnostic skill.",
    tags=("core", "diagnostic"),
)
async def echo(inp: EchoIn, ctx: SkillContext) -> EchoOut:
    return EchoOut(text=inp.text)


class TimeIn(SkillInput):
    timezone: str = Field(default="UTC", description="IANA timezone, e.g. America/New_York")


class TimeOut(SkillOutput):
    iso: str
    timezone: str
    unix: float


@skill(
    name="core.current_time",
    description="Get the current date and time in a timezone.",
    tags=("core",),
)
async def current_time(inp: TimeIn, ctx: SkillContext) -> TimeOut:
    try:
        tz = ZoneInfo(inp.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationFailed(f"Unknown timezone '{inp.timezone}'") from None
    now = datetime.now(UTC).astimezone(tz)
    return TimeOut(iso=now.isoformat(), timezone=inp.timezone, unix=now.timestamp())
