"""Household roles (ADR 0034): built-in presets plus household-defined custom
roles bundling rights. Admin is immutable; a role in use can't be deleted."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, rights, undo_actions
from family_cfo_api.deps import get_engine, require_any_right, require_right
from family_cfo_api.schemas import (
    ErrorResponse,
    Role,
    RoleCreateRequest,
    RoleListResponse,
    RoleUpdateRequest,
)

router = APIRouter(tags=["Household"])


def _to_schema(record: repository.RoleRecord) -> Role:
    return Role(
        id=record.id,
        name=record.name,
        rights=sorted(record.rights),
        built_in=record.built_in,
        member_count=record.member_count,
    )


def _validate_rights(requested: list[str]) -> set[str]:
    # Box-level rights (ADR 0065) are silently dropped rather than rejected:
    # roles saved before the system-admin split may still carry the legacy
    # ai_runtime.manage string, and re-saving such a role must not 422.
    cleaned = set(requested) - rights.BOX_RIGHTS
    unknown = cleaned - rights.ALL_RIGHTS
    if unknown:
        raise HTTPException(
            status_code=422, detail=f"Unknown rights: {', '.join(sorted(unknown))}"
        )
    return cleaned


@router.get(
    "/household/roles",
    operation_id="listRoles",
    response_model=RoleListResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="List the household's roles and the rights catalog",
)
async def list_roles(
    session: repository.SessionContext = Depends(
        require_any_right(rights.MEMBERS_MANAGE, rights.ROLES_MANAGE)
    ),
    engine: Engine = Depends(get_engine),
) -> RoleListResponse:
    return RoleListResponse(
        roles=[_to_schema(r) for r in repository.list_roles(engine, session.household_id)],
        all_rights=sorted(rights.ALL_RIGHTS),
    )


@router.post(
    "/household/roles",
    operation_id="createRole",
    response_model=Role,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {"description": "Role name already in use", "model": ErrorResponse},
    },
    summary="Create a custom role",
)
async def create_role(
    payload: RoleCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.ROLES_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Role:
    role_rights = _validate_rights(payload.rights)
    record = repository.create_role(engine, session.household_id, payload.name, role_rights)
    if record is None:
        raise HTTPException(status_code=409, detail="A role with that name already exists")
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "role.created",
        "role",
        record.id,
        f"Created role '{record.name}'",
        undo_token=undo_actions.created("role", record.id),
    )
    return _to_schema(record)


@router.patch(
    "/household/roles/{role_id}",
    operation_id="updateRole",
    response_model=Role,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Role not found", "model": ErrorResponse},
        409: {"description": "Built-in roles can't be edited", "model": ErrorResponse},
    },
    summary="Update a custom role",
)
async def update_role(
    role_id: str,
    payload: RoleUpdateRequest,
    session: repository.SessionContext = Depends(require_right(rights.ROLES_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Role:
    existing = repository.get_role(engine, session.household_id, role_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if existing.built_in:
        raise HTTPException(status_code=409, detail="Built-in roles can't be edited")
    role_rights = _validate_rights(payload.rights) if payload.rights is not None else None
    repository.update_role(
        engine, session.household_id, role_id, name=payload.name, role_rights=role_rights
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "role.updated",
        "role",
        role_id,
        f"Updated role '{payload.name or existing.name}'",
        undo_token=undo_actions.role_updated(existing),
    )
    updated = repository.get_role(engine, session.household_id, role_id)
    assert updated is not None
    return _to_schema(updated)


@router.delete(
    "/household/roles/{role_id}",
    operation_id="deleteRole",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Role not found", "model": ErrorResponse},
        409: {"description": "Role is built-in or still assigned", "model": ErrorResponse},
    },
    summary="Delete an unused custom role",
)
async def delete_role(
    role_id: str,
    session: repository.SessionContext = Depends(require_right(rights.ROLES_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> None:
    existing = repository.get_role(engine, session.household_id, role_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Role not found")
    if existing.built_in:
        raise HTTPException(status_code=409, detail="Built-in roles can't be deleted")
    if existing.member_count > 0:
        raise HTTPException(
            status_code=409,
            detail="Reassign the members using this role before deleting it",
        )
    repository.delete_role(engine, session.household_id, role_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "role.deleted",
        "role",
        role_id,
        f"Deleted role '{existing.name}'",
        undo_token=undo_actions.role_deleted(existing),
    )
