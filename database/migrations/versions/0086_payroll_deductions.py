"""income_profiles: declared 401(k)/HSA payroll deductions (#6)

The observed savings rate combines transfers, payroll deductions, and the
residual. Payroll deductions (pre-tax retirement + HSA) never reach the bank
feed, so without a declared figure a transfer-only rate understates saving
badly for most households — this is the most important input. Annual minor
units, per earner, matching base_salary_minor.

Revision ID: 0086_payroll_deductions
Revises: 0085_reserve_committed_savings
Create Date: 2026-08-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0086_payroll_deductions"
down_revision: str | None = "0085_reserve_committed_savings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "income_profiles",
        sa.Column(
            "retirement_contribution_annual_minor",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "income_profiles",
        sa.Column(
            "hsa_contribution_annual_minor",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("income_profiles", "hsa_contribution_annual_minor")
    op.drop_column("income_profiles", "retirement_contribution_annual_minor")
