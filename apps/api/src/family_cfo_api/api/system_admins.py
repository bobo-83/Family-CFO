"""Box-level system administrators (ADR 0065).

One vLLM serves every household, so box-global machinery (model swaps) is
guarded by a USER-scoped roster rather than any household role. The first
household's owner is granted at bootstrap; this API manages the roster
afterwards. Managing the roster itself requires being on it.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, rights, undo_actions
from family_cfo_api.deps import get_engine, require_right
from family_cfo_api.schemas import (
    ErrorResponse,
    SystemAdmin,
    SystemAdminGrantRequest,
    SystemAdminList,
)

router = APIRouter(tags=["System Admins"])
logger = logging.getLogger(__name__)


def _to_schema(record: repository.SystemAdminRecord) -> SystemAdmin:
    return SystemAdmin(
        user_id=record.user_id,
        email=record.email,
        display_name=record.display_name,
        granted_at=record.granted_at,
        granted_by_user_id=record.granted_by_user_id,
    )


@router.get(
    "/system/admins",
    operation_id="listSystemAdmins",
    response_model=SystemAdminList,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Not a system administrator", "model": ErrorResponse},
    },
    summary="List the box's system administrators",
)
async def list_system_admins(
    session: repository.SessionContext = Depends(require_right(rights.SYSTEM_ADMIN)),
    engine: Engine = Depends(get_engine),
) -> SystemAdminList:
    return SystemAdminList(admins=[_to_schema(r) for r in repository.list_system_admins(engine)])


@router.post(
    "/system/admins",
    operation_id="grantSystemAdmin",
    response_model=SystemAdmin,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Not a system administrator", "model": ErrorResponse},
        404: {"description": "No user with that email", "model": ErrorResponse},
        409: {"description": "Already a system administrator", "model": ErrorResponse},
    },
    summary="Grant system administrator to an existing user by email",
)
async def grant_system_admin(
    payload: SystemAdminGrantRequest,
    session: repository.SessionContext = Depends(require_right(rights.SYSTEM_ADMIN)),
    engine: Engine = Depends(get_engine),
) -> SystemAdmin:
    user = repository.get_user_by_email(engine, payload.email.strip().lower())
    if user is None:
        # Grants target EXISTING users (e.g. a household admin who signed in
        # before) — inviting new people stays the invites flow (ADR 0056).
        raise HTTPException(status_code=404, detail="No user with that email")
    if not repository.grant_system_admin(engine, user.id, session.user_id):
        raise HTTPException(status_code=409, detail="Already a system administrator")
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "system_admin.granted",
        "system_admin",
        user.id,
        f"Granted system administrator to {user.email}",
        undo_token=undo_actions.created("system_admin", user.id),
    )
    record = next(r for r in repository.list_system_admins(engine) if r.user_id == user.id)
    return _to_schema(record)


@router.delete(
    "/system/admins/{user_id}",
    operation_id="revokeSystemAdmin",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Not a system administrator", "model": ErrorResponse},
        404: {"description": "Not a system administrator (target)", "model": ErrorResponse},
        409: {"description": "The roster must keep at least one admin", "model": ErrorResponse},
    },
    summary="Revoke a user's system administrator grant",
)
async def revoke_system_admin(
    user_id: str,
    session: repository.SessionContext = Depends(require_right(rights.SYSTEM_ADMIN)),
    engine: Engine = Depends(get_engine),
) -> Response:
    admins = {r.user_id: r for r in repository.list_system_admins(engine)}
    target = admins.get(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="That user is not a system administrator")
    if len(admins) <= 1:
        # Self-lockout guard: an empty roster would leave nobody able to
        # manage the runtime or the roster itself.
        raise HTTPException(
            status_code=409, detail="The box must keep at least one system administrator"
        )
    repository.revoke_system_admin(engine, user_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "system_admin.revoked",
        "system_admin",
        user_id,
        f"Revoked system administrator from {target.email}",
        undo_token=undo_actions.system_admin_revoked(user_id, target.granted_by_user_id),
    )
    return Response(status_code=204)
