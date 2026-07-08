from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.engine import Engine

from family_cfo_api import repository, security


async def get_engine(request: Request) -> Engine:
    return request.app.state.db_engine


async def get_current_session(
    request: Request,
    engine: Engine = Depends(get_engine),
) -> repository.SessionContext:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")

    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing or invalid authorization header")

    session = repository.get_session_context(engine, security.hash_token(token))
    if session is None:
        raise HTTPException(status_code=401, detail="Invalid or expired session")

    return session


def require_role(*allowed_roles: str) -> Callable[[repository.SessionContext], repository.SessionContext]:
    async def dependency(
        session: repository.SessionContext = Depends(get_current_session),
    ) -> repository.SessionContext:
        if session.role not in allowed_roles:
            raise HTTPException(status_code=403, detail="Role does not permit this action")

        return session

    return dependency
