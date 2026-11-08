from __future__ import annotations

import hmac
from collections.abc import AsyncIterator

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from jaxon.core.errors import Unauthorized
from jaxon.runtime import Runtime

_bearer = HTTPBearer(auto_error=False)


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


async def get_session(rt: Runtime = Depends(get_runtime)) -> AsyncIterator[AsyncSession]:
    async with rt.db.sessionmaker() as s:
        yield s


async def require_token(
    rt: Runtime = Depends(get_runtime),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    expected = rt.settings.jaxon_api_token
    if expected is None:
        return
    if creds is None or not hmac.compare_digest(creds.credentials, expected.get_secret_value()):
        raise Unauthorized("Missing or invalid API token")
