from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, finance_service, repository, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    ErrorResponse,
    Goal,
    GoalCreateRequest,
    GoalListResponse,
    GoalUpdateRequest,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Goals"])


def _to_goal_schema(engine: Engine, household_id: str, record: repository.GoalRecord) -> Goal:
    # Emergency-fund goals track the reserved fund, not a stale stored current.
    current = finance_service.goal_current_minor(engine, household_id, record)
    return Goal(
        id=record.id,
        name=record.name,
        type=record.goal_type,
        target=MoneySchema(amount_minor=record.target_minor, currency=record.currency),
        current=MoneySchema(amount_minor=current, currency=record.currency),
        target_date=record.target_date,
        priority=record.priority,
        monthly_contribution=(
            MoneySchema(amount_minor=record.monthly_contribution_minor, currency=record.currency)
            if record.monthly_contribution_minor is not None
            else None
        ),
    )


@router.get(
    "/goals",
    operation_id="listGoals",
    response_model=GoalListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List financial goals",
)
async def list_goals(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> GoalListResponse:
    records = repository.list_goals(engine, session.household_id)
    return GoalListResponse(goals=[_to_goal_schema(engine, session.household_id, record) for record in records])


@router.post(
    "/goals",
    operation_id="createGoal",
    response_model=Goal,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Create a financial goal",
)
async def create_goal(
    payload: GoalCreateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Goal:
    record = repository.create_goal(
        engine,
        household_id=session.household_id,
        name=payload.name,
        goal_type=payload.type,
        target_minor=payload.target.amount_minor,
        currency=payload.target.currency,
        target_date=payload.target_date,
        priority=payload.priority,
        monthly_contribution_minor=(
            payload.monthly_contribution.amount_minor
            if payload.monthly_contribution is not None
            else None
        ),
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "goal.created",
        "goal",
        record.id,
        f"Created goal '{record.name}'",
        undo_token=undo_actions.created("goal", record.id),
    )
    return _to_goal_schema(engine, session.household_id, record)


@router.patch(
    "/goals/{goal_id}",
    operation_id="updateGoal",
    response_model=Goal,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Goal not found", "model": ErrorResponse},
    },
    summary="Update a goal (M118: incl. the planned monthly contribution)",
)
async def update_goal(
    goal_id: str,
    payload: GoalUpdateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Goal:
    before = repository.get_goal(engine, session.household_id, goal_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Goal not found")
    contribution_sent = "monthly_contribution" in payload.model_fields_set
    repository.update_goal(
        engine,
        session.household_id,
        goal_id,
        name=payload.name,
        target_minor=payload.target.amount_minor if payload.target is not None else None,
        target_date=(
            payload.target_date
            if "target_date" in payload.model_fields_set
            else repository._UNSET
        ),
        priority=payload.priority,
        monthly_contribution_minor=(
            (payload.monthly_contribution.amount_minor if payload.monthly_contribution else None)
            if contribution_sent
            else repository._UNSET
        ),
    )
    record = repository.get_goal(engine, session.household_id, goal_id)
    assert record is not None
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "goal.updated",
        "goal",
        goal_id,
        f"Updated goal '{record.name}'",
        undo_token=undo_actions.goal_updated(before),
    )
    return _to_goal_schema(engine, session.household_id, record)


@router.delete(
    "/goals/{goal_id}",
    operation_id="deleteGoal",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Goal not found", "model": ErrorResponse},
    },
    summary="Delete a goal",
)
async def delete_goal(
    goal_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    existing = repository.get_goal(engine, session.household_id, goal_id)
    if existing is None or not repository.delete_goal(engine, session.household_id, goal_id):
        raise HTTPException(status_code=404, detail="Goal not found")
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "goal.deleted",
        "goal",
        goal_id,
        f"Deleted goal '{existing.name}'",
        undo_token=undo_actions.goal_deleted(existing),
    )
    return Response(status_code=204)
