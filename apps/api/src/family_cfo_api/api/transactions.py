from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import ErrorResponse
from family_cfo_api.schemas import Money as MoneySchema
from family_cfo_api.schemas import (
    Transaction,
    TransactionCreateRequest,
    TransactionListResponse,
    TransactionUpdateRequest,
)

router = APIRouter(tags=["Transactions"])


def _to_schema(record: repository.TransactionRecord) -> Transaction:
    return Transaction(
        id=record.id,
        account_id=record.account_id,
        occurred_at=record.occurred_at,
        amount=MoneySchema(amount_minor=record.amount_minor, currency=record.currency),
        merchant=record.merchant,
        category=record.category,
        category_id=record.category_id,
        description=record.description,
    )


def _require_category(engine: Engine, household_id: str, category_id: str) -> None:
    if repository.get_category(engine, household_id, category_id) is None:
        raise HTTPException(status_code=404, detail="Category not found")


def _require_account(
    engine: Engine, household_id: str, account_id: str
) -> repository.AccountRecord:
    account = repository.get_account(engine, household_id, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.get(
    "/transactions",
    operation_id="listTransactions",
    response_model=TransactionListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List transactions for the household",
)
async def list_transactions(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> TransactionListResponse:
    records = repository.list_transactions(engine, session.household_id)
    return TransactionListResponse(transactions=[_to_schema(record) for record in records])


@router.post(
    "/transactions",
    operation_id="createTransaction",
    response_model=Transaction,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
    },
    summary="Manually create a transaction",
)
async def create_transaction(
    payload: TransactionCreateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Transaction:
    account = _require_account(engine, session.household_id, payload.account_id)
    if payload.amount.currency != account.currency:
        raise HTTPException(status_code=400, detail=f"Amount currency must be {account.currency}")
    if payload.category_id is not None:
        _require_category(engine, session.household_id, payload.category_id)

    transaction_id = repository.create_transaction(
        engine,
        household_id=session.household_id,
        account_id=payload.account_id,
        occurred_at=payload.occurred_at,
        amount_minor=payload.amount.amount_minor,
        currency=payload.amount.currency,
        merchant=payload.merchant,
        description=payload.description,
        import_source=None,
        import_id=None,
        review_state="reviewed",
        category_id=payload.category_id,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "transaction.created",
        "transaction",
        transaction_id,
        "Created a manual transaction",
    )
    record = repository.get_transaction(engine, session.household_id, transaction_id)
    assert record is not None
    return _to_schema(record)


@router.patch(
    "/transactions/{transaction_id}",
    operation_id="updateTransaction",
    response_model=Transaction,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Transaction not found", "model": ErrorResponse},
    },
    summary="Update a transaction",
)
async def update_transaction(
    transaction_id: str,
    payload: TransactionUpdateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Transaction:
    if repository.get_transaction(engine, session.household_id, transaction_id) is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if payload.account_id is not None:
        _require_account(engine, session.household_id, payload.account_id)
    if payload.category_id is not None:
        _require_category(engine, session.household_id, payload.category_id)

    amount_minor = payload.amount.amount_minor if payload.amount is not None else None
    currency = payload.amount.currency if payload.amount is not None else None
    repository.update_transaction(
        engine,
        session.household_id,
        transaction_id,
        account_id=payload.account_id,
        occurred_at=payload.occurred_at,
        amount_minor=amount_minor,
        currency=currency,
        merchant=payload.merchant,
        description=payload.description,
        category_id=payload.category_id,
        clear_category=payload.clear_category,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "transaction.updated",
        "transaction",
        transaction_id,
        "Updated a transaction",
    )
    record = repository.get_transaction(engine, session.household_id, transaction_id)
    assert record is not None
    return _to_schema(record)


@router.delete(
    "/transactions/{transaction_id}",
    operation_id="deleteTransaction",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Transaction not found", "model": ErrorResponse},
    },
    summary="Delete a transaction",
)
async def delete_transaction(
    transaction_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    if repository.get_transaction(engine, session.household_id, transaction_id) is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    repository.delete_transaction(engine, session.household_id, transaction_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "transaction.deleted",
        "transaction",
        transaction_id,
        "Deleted a transaction",
    )
    return Response(status_code=204)
