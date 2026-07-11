"""Deterministic US federal tax estimate (M61).

2026 tax-year parameters (IRS Rev. Proc. 2025-32): federal brackets, standard
deduction, employee-side FICA. Deliberately an ESTIMATE with stated
assumptions — standard deduction only, no credits, no itemizing, no state or
local tax — because the app cannot know a household's full tax situation.
Bank deposits are usually take-home pay, so ``gross_up_from_net`` solves
``gross − tax(gross) = net`` to recover the pre-tax income the tax applies to.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from family_cfo_financial_engine.money import Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult

TAX_YEAR = 2026

FILING_STATUSES = ("single", "married_joint", "head_of_household")

# Minor units (cents). None upper bound = top bracket.
_BRACKETS: dict[str, list[tuple[int | None, Decimal]]] = {
    "single": [
        (12_400_00, Decimal("0.10")),
        (50_400_00, Decimal("0.12")),
        (105_700_00, Decimal("0.22")),
        (201_775_00, Decimal("0.24")),
        (256_225_00, Decimal("0.32")),
        (640_600_00, Decimal("0.35")),
        (None, Decimal("0.37")),
    ],
    "married_joint": [
        (24_800_00, Decimal("0.10")),
        (100_800_00, Decimal("0.12")),
        (211_400_00, Decimal("0.22")),
        (403_550_00, Decimal("0.24")),
        (512_450_00, Decimal("0.32")),
        (768_700_00, Decimal("0.35")),
        (None, Decimal("0.37")),
    ],
    "head_of_household": [
        (17_700_00, Decimal("0.10")),
        (67_450_00, Decimal("0.12")),
        (105_700_00, Decimal("0.22")),
        (201_775_00, Decimal("0.24")),
        (256_200_00, Decimal("0.32")),
        (640_600_00, Decimal("0.35")),
        (None, Decimal("0.37")),
    ],
}

_STANDARD_DEDUCTION: dict[str, int] = {
    "single": 16_100_00,
    "married_joint": 32_200_00,
    "head_of_household": 24_150_00,
}

_SS_WAGE_BASE = 184_500_00
_SS_RATE = Decimal("0.062")
_MEDICARE_RATE = Decimal("0.0145")
_ADDL_MEDICARE_RATE = Decimal("0.009")
_ADDL_MEDICARE_THRESHOLD: dict[str, int] = {
    "single": 200_000_00,
    "married_joint": 250_000_00,
    "head_of_household": 200_000_00,
}

_ASSUMPTIONS = [
    f"US federal estimate for tax year {TAX_YEAR} (IRS Rev. Proc. 2025-32 parameters).",
    "Standard deduction only — no itemizing, no credits, no other income adjustments.",
    "Employee-side FICA (6.2% Social Security to the wage base, 1.45% Medicare "
    "plus 0.9% additional over the threshold); self-employment tax not modeled.",
    "State and local taxes are NOT included.",
]


def _validate(filing_status: str) -> None:
    if filing_status not in FILING_STATUSES:
        raise ValueError(f"filing_status must be one of {FILING_STATUSES}")


def _to_minor(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _federal_income_tax_minor(gross_minor: int, filing_status: str) -> int:
    taxable = max(0, gross_minor - _STANDARD_DEDUCTION[filing_status])
    tax = Decimal(0)
    lower = 0
    for upper, rate in _BRACKETS[filing_status]:
        span_top = taxable if upper is None else min(taxable, upper)
        if span_top > lower:
            tax += (Decimal(span_top) - Decimal(lower)) * rate
        if upper is None or taxable <= upper:
            break
        lower = upper
    return _to_minor(tax)


def _fica_tax_minor(gross_minor: int, filing_status: str) -> int:
    social_security = Decimal(min(gross_minor, _SS_WAGE_BASE)) * _SS_RATE
    medicare = Decimal(gross_minor) * _MEDICARE_RATE
    over_threshold = max(0, gross_minor - _ADDL_MEDICARE_THRESHOLD[filing_status])
    medicare += Decimal(over_threshold) * _ADDL_MEDICARE_RATE
    return _to_minor(social_security + medicare)


def _total_tax_minor(gross_minor: int, filing_status: str) -> int:
    return _federal_income_tax_minor(gross_minor, filing_status) + _fica_tax_minor(
        gross_minor, filing_status
    )


def estimate_annual_tax(gross_annual: Money, filing_status: str) -> CalculationResult:
    """Federal income tax + employee FICA on an annual GROSS income."""
    _validate(filing_status)
    currency = gross_annual.currency
    gross = max(0, gross_annual.amount_minor)
    federal = _federal_income_tax_minor(gross, filing_status)
    fica = _fica_tax_minor(gross, filing_status)
    total = federal + fica
    effective_rate = round(total / gross, 4) if gross > 0 else 0.0
    return CalculationResult(
        calculation_type="annual_tax_estimate",
        version=CALCULATION_ENGINE_VERSION,
        inputs={"filing_status": filing_status, "tax_year": TAX_YEAR, "currency": currency},
        assumptions=list(_ASSUMPTIONS),
        outputs={
            "gross_income": Money(gross, currency),
            "standard_deduction": Money(_STANDARD_DEDUCTION[filing_status], currency),
            "taxable_income": Money(
                max(0, gross - _STANDARD_DEDUCTION[filing_status]), currency
            ),
            "federal_income_tax": Money(federal, currency),
            "fica_tax": Money(fica, currency),
            "total_tax": Money(total, currency),
            "effective_rate": effective_rate,
        },
        warnings=[],
    )


def gross_up_from_net(net_annual: Money, filing_status: str) -> CalculationResult:
    """Recover gross income from annual take-home pay, then estimate its tax.

    Fixed-point iteration on gross = net + tax(gross); every marginal rate is
    below 100%, so the sequence converges. Take-home deposits also reflect
    pre-tax items this app cannot see (401k, health premiums), so the real
    gross is likely HIGHER — stated in the assumptions.
    """
    _validate(filing_status)
    currency = net_annual.currency
    net = max(0, net_annual.amount_minor)
    gross = net
    for _ in range(100):
        next_gross = net + _total_tax_minor(gross, filing_status)
        if abs(next_gross - gross) <= 1:
            gross = next_gross
            break
        gross = next_gross
    result = estimate_annual_tax(Money(gross, currency), filing_status)
    outputs = dict(result.outputs)
    outputs["net_income"] = Money(net, currency)
    return CalculationResult(
        calculation_type="annual_tax_estimate_from_net",
        version=CALCULATION_ENGINE_VERSION,
        inputs=dict(result.inputs),
        assumptions=[
            "Income was treated as TAKE-HOME pay; gross was recovered by solving "
            "gross − tax(gross) = net.",
            "Pre-tax deductions the deposits already reflect (401k, health "
            "premiums, HSA) are invisible here, so actual gross income and tax "
            "withheld are likely somewhat higher.",
            *_ASSUMPTIONS,
        ],
        outputs=outputs,
        warnings=[],
    )
