from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import ErrorResponse, Goal, GoalCreateRequest, GoalListResponse
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Goals"])


def _to_goal_schema(record: repository.GoalRecord) -> Goal:
    return Goal(
        id=record.id,
        name=record.name,
        type=record.goal_type,
        target=MoneySchema(amount_minor=record.target_minor, currency=record.currency),
        current=MoneySchema(amount_minor=record.current_minor, currency=record.currency),
        target_date=record.target_date,
        priority=record.priority,
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
    return GoalListResponse(goals=[_to_goal_schema(record) for record in records])


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
    )
    return _to_goal_schema(record)
