from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, report_generation
from family_cfo_api.ai_runtime_selection import select_explanation_adapter
from family_cfo_api.deps import get_current_session, get_engine, require_role
from family_cfo_api.schemas import (
    ErrorResponse,
    GoalProgressSummary,
    Report,
    ReportGenerateRequest,
    ReportListResponse,
    ReportSummary,
)
from family_cfo_api.schemas import Money as MoneySchema

router = APIRouter(tags=["Reports"])
logger = logging.getLogger(__name__)


def _to_schema(record: repository.ReportRecord) -> Report:
    summary = record.summary
    return Report(
        id=record.id,
        report_type=record.report_type,
        period_start=record.period_start,
        period_end=record.period_end,
        explanation_text=record.explanation_text,
        explanation_source=record.explanation_source,
        generated_at=record.generated_at,
        summary=ReportSummary(
            wins=summary["wins"],
            risks=summary["risks"],
            unusual_spending=summary["unusual_spending"],
            recommended_actions=summary["recommended_actions"],
            goal_progress=[GoalProgressSummary(**goal) for goal in summary["goal_progress"]],
            net_cash_flow=MoneySchema(**summary["net_cash_flow"]),
            calculation_refs=summary["calculation_refs"],
        ),
    )


@router.get(
    "/reports",
    operation_id="listReports",
    response_model=ReportListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List generated reports",
)
async def list_reports(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> ReportListResponse:
    records = repository.list_reports(engine, session.household_id)
    return ReportListResponse(reports=[_to_schema(record) for record in records])


@router.get(
    "/reports/{report_id}",
    operation_id="getReport",
    response_model=Report,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "Report not found", "model": ErrorResponse},
    },
    summary="Get a generated report",
)
async def get_report(
    report_id: str,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> Report:
    record = repository.get_report(engine, session.household_id, report_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return _to_schema(record)


@router.post(
    "/reports/generate",
    operation_id="generateReport",
    response_model=Report,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Household not found", "model": ErrorResponse},
    },
    summary="Generate (or regenerate) a weekly or monthly report",
)
async def generate_report(
    payload: ReportGenerateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Report:
    explanation_adapter, runtime_client = select_explanation_adapter(engine, session.household_id)
    try:
        record = report_generation.generate_report(
            engine, session.household_id, payload.report_type, explanation_adapter
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    finally:
        if runtime_client is not None:
            runtime_client.close()

    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "report.generated",
        "report",
        record.id,
        f"Generated {payload.report_type} report",
    )
    logger.info(
        "report generated household_id=%s report_id=%s report_type=%s explanation_source=%s",
        session.household_id,
        record.id,
        record.report_type,
        record.explanation_source,
    )

    return _to_schema(record)
