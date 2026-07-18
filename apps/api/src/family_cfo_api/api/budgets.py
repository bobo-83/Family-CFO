"""Budget envelopes (M46): monthly per-category limits with soft tracking.

Product decisions (2026-07-09): monthly calendar periods, no rollover, and
threshold warnings — a recording app cannot block spending, only surface it.
"""

from __future__ import annotations

import calendar
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, rights, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_right
from family_cfo_api.schemas import (
    Budget,
    BudgetCreateRequest,
    BudgetListResponse,
    BudgetUpdateRequest,
    ErrorResponse,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Budgets"])

WARNING_THRESHOLD = 0.8


def _month_window(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    start = today.replace(day=1)
    end = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    return start, end


def budget_progress(
    record: repository.BudgetRecord, spent_minor: int
) -> Budget:
    """Envelope progress for the current month; raw percent drives the status."""
    limit = record.limit_minor
    percent = round(spent_minor / limit * 100) if limit > 0 else 0
    if limit > 0 and spent_minor > limit:
        status = "over"
    elif limit > 0 and spent_minor >= limit * WARNING_THRESHOLD:
        status = "warning"
    else:
        status = "under"
    return Budget(
        id=record.id,
        category_id=record.category_id,
        category_name=record.category_name,
        limit=MoneySchema(amount_minor=limit, currency=record.currency),
        spent=MoneySchema(amount_minor=spent_minor, currency=record.currency),
        remaining=MoneySchema(amount_minor=limit - spent_minor, currency=record.currency),
        percent_used=percent,
        status=status,
    )


def budgets_with_progress(
    engine: Engine, household_id: str, currency: str, *, today: date | None = None
) -> list[Budget]:
    records = repository.list_budgets(engine, household_id)
    if not records:
        return []
    start, end = _month_window(today)
    spent_by_category = repository.sum_spending_by_category(
        engine, household_id, start, end, currency
    )
    return [
        budget_progress(record, spent_by_category.get(record.category_id, 0))
        for record in records
    ]


def _household_currency(engine: Engine, household_id: str) -> str:
    household = repository.get_household(engine, household_id)
    return household.base_currency if household else "USD"


@router.get(
    "/budgets",
    operation_id="listBudgets",
    response_model=BudgetListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List budget envelopes with current-month progress",
)
async def list_budgets(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> BudgetListResponse:
    currency = _household_currency(engine, session.household_id)
    return BudgetListResponse(
        budgets=budgets_with_progress(engine, session.household_id, currency)
    )


@router.post(
    "/budgets",
    operation_id="createBudget",
    response_model=Budget,
    status_code=201,
    responses={
        400: {"description": "Invalid request", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Category not found", "model": ErrorResponse},
        409: {"description": "This category already has a budget", "model": ErrorResponse},
    },
    summary="Create a monthly budget envelope for a category",
)
async def create_budget(
    payload: BudgetCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.BUDGETS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Budget:
    if payload.limit.amount_minor <= 0:
        raise HTTPException(status_code=400, detail="Budget limit must be positive")
    category = repository.get_category(engine, session.household_id, payload.category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if repository.budget_exists_for_category(engine, session.household_id, payload.category_id):
        raise HTTPException(status_code=409, detail="This category already has a budget")

    budget_id = repository.create_budget(
        engine,
        session.household_id,
        payload.category_id,
        payload.limit.amount_minor,
        payload.limit.currency,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "budget.created",
        "budget",
        budget_id,
        f"Created a budget for '{category.name}'",
        undo_token=undo_actions.created("budget", budget_id),
    )
    record = repository.get_budget(engine, session.household_id, budget_id)
    assert record is not None
    currency = _household_currency(engine, session.household_id)
    start, end = _month_window()
    spent = repository.sum_spending_by_category(
        engine, session.household_id, start, end, currency
    ).get(record.category_id, 0)
    return budget_progress(record, spent)


@router.patch(
    "/budgets/{budget_id}",
    operation_id="updateBudget",
    response_model=Budget,
    responses={
        400: {"description": "Invalid request", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Budget not found", "model": ErrorResponse},
    },
    summary="Change a budget envelope's monthly limit",
)
async def update_budget(
    budget_id: str,
    payload: BudgetUpdateRequest,
    session: repository.SessionContext = Depends(require_right(rights.BUDGETS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Budget:
    if payload.limit.amount_minor <= 0:
        raise HTTPException(status_code=400, detail="Budget limit must be positive")
    before = repository.get_budget(engine, session.household_id, budget_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Budget not found")
    repository.update_budget_limit(
        engine, session.household_id, budget_id, payload.limit.amount_minor
    )
    record = repository.get_budget(engine, session.household_id, budget_id)
    assert record is not None
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "budget.updated",
        "budget",
        budget_id,
        f"Changed the budget for “{record.category_name}”",
        undo_token=undo_actions.budget_updated(before),
    )
    currency = _household_currency(engine, session.household_id)
    start, end = _month_window()
    spent = repository.sum_spending_by_category(
        engine, session.household_id, start, end, currency
    ).get(record.category_id, 0)
    return budget_progress(record, spent)


@router.delete(
    "/budgets/{budget_id}",
    operation_id="deleteBudget",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Budget not found", "model": ErrorResponse},
    },
    summary="Delete a budget envelope",
)
async def delete_budget(
    budget_id: str,
    session: repository.SessionContext = Depends(require_right(rights.BUDGETS_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    existing = repository.get_budget(engine, session.household_id, budget_id)
    if not repository.delete_budget(engine, session.household_id, budget_id):
        raise HTTPException(status_code=404, detail="Budget not found")
    category = existing.category_name if existing is not None else "a category"
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "budget.deleted",
        "budget",
        budget_id,
        f"Deleted the budget for “{category}”",
        undo_token=undo_actions.budget_deleted(existing) if existing is not None else None,
    )
    return Response(status_code=204)
