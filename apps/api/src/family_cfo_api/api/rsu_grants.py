"""Grant-based RSU tracking endpoints (M-rsu-grants).

The earner enters each grant; the derived vest schedule is editable row by
row; a cached live quote values everything. See rsu_service for how the
valuation replaces the flat rsu_annual_minor figure across the app.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, rights, rsu_service, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_right
from family_cfo_api.schemas import (
    ErrorResponse,
    RsuGrant,
    RsuGrantCreateRequest,
    RsuGrantsResponse,
    RsuVestEvent,
    RsuVestEventCreateRequest,
    RsuVestEventUpdateRequest,
    StockQuote,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Income"])


def _grant_schema(
    grant: repository.RsuGrantRecord,
    events: list[repository.RsuVestEventRecord],
    quote: repository.StockQuoteRecord | None,
) -> RsuGrant:
    return RsuGrant(
        id=grant.id,
        earner_id=grant.income_profile_id,
        ticker=grant.ticker,
        units=grant.units,
        grant_date=grant.grant_date,
        vest_years=grant.vest_years,
        frequency=grant.frequency,
        events=[
            RsuVestEvent(
                id=event.id,
                grant_id=event.grant_id,
                vest_date=event.vest_date,
                units=event.units,
                value=(
                    MoneySchema(
                        amount_minor=event.units * quote.price_minor, currency=quote.currency
                    )
                    if quote
                    else None
                ),
            )
            for event in events
            if event.grant_id == grant.id
        ],
    )


def _grants_response(engine: Engine, household_id: str) -> RsuGrantsResponse:
    from datetime import date

    valuation = rsu_service.load_valuation(engine, household_id)
    today = date.today()
    derived = 0
    have_any = False
    for profile in repository.list_income_profiles(engine, household_id):
        annual = valuation.upcoming_annual_minor(profile.id, today=today)
        if annual is not None:
            derived += annual
            have_any = True
    base_currency = next((q.currency for q in valuation.quotes.values()), "USD")
    return RsuGrantsResponse(
        grants=[
            _grant_schema(grant, valuation.events, valuation.quotes.get(grant.ticker))
            for grant in valuation.grants
        ],
        quotes=[
            StockQuote(
                ticker=quote.ticker,
                price=MoneySchema(amount_minor=quote.price_minor, currency=quote.currency),
                as_of=quote.as_of,
                source=quote.source,
            )
            for quote in valuation.quotes.values()
        ],
        derived_annual=(
            MoneySchema(amount_minor=derived, currency=base_currency) if have_any else None
        ),
    )


@router.get(
    "/income/rsu-grants",
    operation_id="listRsuGrants",
    response_model=RsuGrantsResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="RSU grants with their editable vest schedules and live quotes",
)
async def list_rsu_grants(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> RsuGrantsResponse:
    return _grants_response(engine, session.household_id)


@router.post(
    "/income/rsu-grants",
    operation_id="createRsuGrant",
    response_model=RsuGrantsResponse,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Earner not found", "model": ErrorResponse},
    },
    summary="Enter an RSU grant; the vest schedule is derived, then editable",
)
async def create_rsu_grant(
    payload: RsuGrantCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> RsuGrantsResponse:
    profile = next(
        (
            p
            for p in repository.list_income_profiles(engine, session.household_id)
            if p.id == payload.earner_id
        ),
        None,
    )
    if profile is None:
        raise HTTPException(status_code=404, detail="Earner not found")
    ticker = payload.ticker.strip().upper()
    events = rsu_service.derive_vest_schedule(
        payload.units, payload.grant_date, payload.vest_years, payload.frequency
    )
    record = repository.create_rsu_grant(
        engine,
        session.household_id,
        income_profile_id=payload.earner_id,
        ticker=ticker,
        units=payload.units,
        grant_date=payload.grant_date,
        vest_years=payload.vest_years,
        frequency=payload.frequency,
        events=events,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "rsu_grant.created",
        "rsu_grant",
        record.id,
        f"Added RSU grant: {payload.units} {ticker}",
        undo_token=undo_actions.created("rsu_grant", record.id),
    )
    # The grant is worthless to the UI without a price — fetch one now
    # (best-effort; sync keeps it fresh afterward).
    rsu_service.refresh_quotes(engine, session.household_id)
    return _grants_response(engine, session.household_id)


@router.delete(
    "/income/rsu-grants/{grant_id}",
    operation_id="deleteRsuGrant",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Grant not found", "model": ErrorResponse},
    },
    summary="Delete an RSU grant and its vest schedule",
)
async def delete_rsu_grant(
    grant_id: str,
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    grant = repository.get_rsu_grant(engine, session.household_id, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant not found")
    events = [
        e
        for e in repository.list_rsu_vest_events(engine, session.household_id)
        if e.grant_id == grant_id
    ]
    repository.delete_rsu_grant(engine, session.household_id, grant_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "rsu_grant.deleted",
        "rsu_grant",
        grant_id,
        f"Deleted RSU grant: {grant.units} {grant.ticker}",
        undo_token=undo_actions.rsu_grant_deleted(grant, events),
    )
    return Response(status_code=204)


@router.post(
    "/income/rsu-grants/{grant_id}/vest-events",
    operation_id="addRsuVestEvent",
    response_model=RsuVestEvent,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Grant not found", "model": ErrorResponse},
    },
    summary="Add a vest event to a grant's schedule",
)
async def add_rsu_vest_event(
    grant_id: str,
    payload: RsuVestEventCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> RsuVestEvent:
    grant = repository.get_rsu_grant(engine, session.household_id, grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="Grant not found")
    record = repository.add_rsu_vest_event(
        engine, session.household_id, grant_id, payload.vest_date, payload.units
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "rsu_vest_event.created",
        "rsu_vest_event",
        record.id,
        f"Added a vest of {payload.units} {grant.ticker} on {payload.vest_date.isoformat()}",
        undo_token=undo_actions.created("rsu_vest_event", record.id),
    )
    return RsuVestEvent(
        id=record.id, grant_id=grant_id, vest_date=record.vest_date, units=record.units
    )


@router.patch(
    "/income/rsu-vest-events/{event_id}",
    operation_id="updateRsuVestEvent",
    response_model=RsuVestEvent,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Vest event not found", "model": ErrorResponse},
    },
    summary="Edit a vest event (date and/or units)",
)
async def update_rsu_vest_event(
    event_id: str,
    payload: RsuVestEventUpdateRequest,
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> RsuVestEvent:
    before = repository.get_rsu_vest_event(engine, session.household_id, event_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Vest event not found")
    repository.update_rsu_vest_event(
        engine,
        session.household_id,
        event_id,
        vest_date=payload.vest_date,
        units=payload.units,
    )
    after = repository.get_rsu_vest_event(engine, session.household_id, event_id)
    assert after is not None
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "rsu_vest_event.updated",
        "rsu_vest_event",
        event_id,
        f"Edited a vest: {after.units} units on {after.vest_date.isoformat()}",
        undo_token=undo_actions.rsu_vest_event_updated(before),
    )
    return RsuVestEvent(
        id=after.id, grant_id=after.grant_id, vest_date=after.vest_date, units=after.units
    )


@router.delete(
    "/income/rsu-vest-events/{event_id}",
    operation_id="deleteRsuVestEvent",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Vest event not found", "model": ErrorResponse},
    },
    summary="Remove a vest event from the schedule",
)
async def delete_rsu_vest_event(
    event_id: str,
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    before = repository.get_rsu_vest_event(engine, session.household_id, event_id)
    if before is None:
        raise HTTPException(status_code=404, detail="Vest event not found")
    repository.delete_rsu_vest_event(engine, session.household_id, event_id)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "rsu_vest_event.deleted",
        "rsu_vest_event",
        event_id,
        f"Removed a vest of {before.units} units on {before.vest_date.isoformat()}",
        undo_token=undo_actions.rsu_vest_event_deleted(before),
    )
    return Response(status_code=204)


@router.post(
    "/income/rsu-quotes/refresh",
    operation_id="refreshRsuQuotes",
    response_model=RsuGrantsResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
    },
    summary="Refresh the live quotes behind the RSU valuation",
)
async def refresh_rsu_quotes(
    session: repository.SessionContext = Depends(require_right(rights.INCOME_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> RsuGrantsResponse:
    rsu_service.refresh_quotes(engine, session.household_id)
    return _grants_response(engine, session.household_id)
