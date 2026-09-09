"""Qualification gates shared by advisor recommendation entry points.

These helpers perform no financial arithmetic. They only union Item 2's
request-local source sets so an advisor cannot invoke a decision calculator on
incomplete monetary inputs.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine

from family_cfo_api import finance_service
from family_cfo_api.qualified_amounts import SourceSet, union_sources


def purchase_impact_incomplete_sources(
    engine: Engine, household_id: str, currency: str
) -> SourceSet:
    """Sources that make purchase affordability/coverage advice unavailable."""
    monthly_expenses = finance_service.monthly_essential_expenses(
        engine, household_id, currency
    )
    return union_sources(monthly_expenses)
