from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    Account,
    AccountBalanceCreateRequest,
    AccountCreateRequest,
    AccountListResponse,
    AccountUpdateRequest,
    ErrorResponse,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Accounts"])


def _min_payment(currency: str, minor: int | None) -> MoneySchema | None:
    return None if minor is None else MoneySchema(amount_minor=minor, currency=currency)


def _account_schema(record: repository.AccountRecord, balance_minor: int) -> Account:
    return Account(
        id=record.id,
        name=record.name,
        type=record.account_type,
        balance=MoneySchema(amount_minor=balance_minor, currency=record.currency),
        annual_interest_rate=record.annual_interest_rate,
        minimum_payment=_min_payment(record.currency, record.minimum_payment_minor),
    )


@router.get(
    "/accounts",
    operation_id="listAccounts",
    response_model=AccountListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List accounts",
)
async def list_accounts(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> AccountListResponse:
    balances = repository.list_account_balances(engine, session.household_id)
    connections = repository.account_connection_map(engine, session.household_id)
    return AccountListResponse(
        accounts=[
            Account(
                id=balance.account_id,
                name=balance.name,
                type=balance.account_type,
                balance=MoneySchema(amount_minor=balance.balance_minor, currency=balance.currency),
                annual_interest_rate=balance.annual_interest_rate,
                minimum_payment=_min_payment(balance.currency, balance.minimum_payment_minor),
                institution=(info := connections.get(balance.account_id)) and info.institution,
                last_synced_at=info.last_synced_at if info else None,
            )
            for balance in balances
        ]
    )


@router.post(
    "/accounts",
    operation_id="createAccount",
    response_model=Account,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Create an account",
)
async def create_account(
    payload: AccountCreateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Account:
    if payload.minimum_payment is not None and payload.minimum_payment.currency != payload.currency:
        raise HTTPException(
            status_code=400, detail=f"minimum_payment currency must be {payload.currency}"
        )
    record = repository.create_account(
        engine,
        household_id=session.household_id,
        name=payload.name,
        account_type=payload.type,
        currency=payload.currency,
        annual_interest_rate=payload.annual_interest_rate,
        minimum_payment_minor=(
            payload.minimum_payment.amount_minor if payload.minimum_payment is not None else None
        ),
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "account.created",
        "account",
        record.id,
        f"Created account '{record.name}'",
    )
    return _account_schema(record, balance_minor=0)


@router.patch(
    "/accounts/{account_id}",
    operation_id="updateAccount",
    response_model=Account,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
    },
    summary="Update an account",
)
async def update_account(
    account_id: str,
    payload: AccountUpdateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Account:
    existing = repository.get_account(engine, session.household_id, account_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if (
        payload.minimum_payment is not None
        and payload.minimum_payment.currency != existing.currency
    ):
        raise HTTPException(
            status_code=400, detail=f"minimum_payment currency must be {existing.currency}"
        )
    repository.update_account(
        engine,
        session.household_id,
        account_id,
        name=payload.name,
        account_type=payload.type,
        annual_interest_rate=payload.annual_interest_rate,
        minimum_payment_minor=(
            payload.minimum_payment.amount_minor if payload.minimum_payment is not None else None
        ),
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "account.updated",
        "account",
        account_id,
        "Updated account",
    )
    record = repository.get_account(engine, session.household_id, account_id)
    assert record is not None
    balance_minor = repository.get_latest_balance_minor(engine, account_id)
    return _account_schema(record, balance_minor)


@router.delete(
    "/accounts/{account_id}",
    operation_id="deleteAccount",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
        409: {"description": "Account is referenced by other records", "model": ErrorResponse},
    },
    summary="Delete an account",
)
async def delete_account(
    account_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    if repository.get_account(engine, session.household_id, account_id) is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if repository.account_in_use(engine, account_id):
        raise HTTPException(
            status_code=409,
            detail="Account is referenced by transactions, bills, or imports; reassign or delete those first",
        )
    repository.delete_account(engine, session.household_id, account_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "account.deleted",
        "account",
        account_id,
        "Deleted account",
    )
    return Response(status_code=204)


@router.post(
    "/accounts/{account_id}/balances",
    operation_id="recordAccountBalance",
    response_model=Account,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
    },
    summary="Record a new balance for an account",
)
async def record_account_balance(
    account_id: str,
    payload: AccountBalanceCreateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Account:
    record = repository.get_account(engine, session.household_id, account_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Account not found")
    if payload.balance.currency != record.currency:
        raise HTTPException(status_code=400, detail=f"Balance currency must be {record.currency}")
    repository.record_account_balance(engine, account_id, payload.balance.amount_minor)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "account.balance_recorded",
        "account",
        account_id,
        "Recorded a new account balance",
    )
    return _account_schema(record, payload.balance.amount_minor)
