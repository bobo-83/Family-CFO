"""Deterministic US federal tax estimate (M61).

2026 tax-year parameters (IRS Rev. Proc. 2025-32): federal brackets, standard
deduction, employee-side FICA. Deliberately an ESTIMATE with stated
assumptions — standard deduction only, no credits, no itemizing, no state or
local tax — because the app cannot know a household's full tax situation.
Bank deposits are usually take-home pay, so ``gross_up_from_net`` solves
``gross − tax(gross) = net`` to recover the pre-tax income the tax applies to.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from family_cfo_financial_engine.money import Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult

# Every parameter below was cross-checked against PRIMARY sources on
# 2026-07-12 (M80). Yearly refresh procedure:
# docs/guides/tax-parameter-updates.md. When the calendar year passes
# TAX_YEAR, every estimate carries a STALE PARAMETERS warning until this
# module is updated.
#
# - Federal brackets: Rev. Proc. 2025-32 §4.01 Tables 1-3
#   (https://www.irs.gov/pub/irs-drop/rp-25-32.pdf)
# - Standard deduction: Rev. Proc. 2025-32 §4.14
# - Social Security wage base: SSA 2026 announcement ($184,500)
#   (https://www.ssa.gov/oact/cola/cbb.html)
# - Additional Medicare thresholds: IRC §3101(b)(2) — statutory, NOT indexed
# - California: FTB 2025 indexed parameters (3.0% CCPI June 2024→June 2025)

TAX_YEAR = 2026

FILING_STATUSES = ("single", "married_joint", "head_of_household")

# Minor units (cents). None upper bound = top bracket.
_BRACKETS: dict[str, list[tuple[int | None, Decimal]]] = {
    # Rev. Proc. 2025-32 §4.01 Table 3.
    "single": [
        (12_400_00, Decimal("0.10")),
        (50_400_00, Decimal("0.12")),
        (105_700_00, Decimal("0.22")),
        (201_775_00, Decimal("0.24")),
        (256_225_00, Decimal("0.32")),
        (640_600_00, Decimal("0.35")),
        (None, Decimal("0.37")),
    ],
    # Rev. Proc. 2025-32 §4.01 Table 1.
    "married_joint": [
        (24_800_00, Decimal("0.10")),
        (100_800_00, Decimal("0.12")),
        (211_400_00, Decimal("0.22")),
        (403_550_00, Decimal("0.24")),
        (512_450_00, Decimal("0.32")),
        (768_700_00, Decimal("0.35")),
        (None, Decimal("0.37")),
    ],
    # Rev. Proc. 2025-32 §4.01 Table 2. NOTE: the 24% bracket tops at
    # $201,750 — NOT the single filer's $201,775 (several secondary sources
    # get this wrong; the M80 cross-check fixed it here).
    "head_of_household": [
        (17_700_00, Decimal("0.10")),
        (67_450_00, Decimal("0.12")),
        (105_700_00, Decimal("0.22")),
        (201_750_00, Decimal("0.24")),
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

# --- State income tax (M65) ---------------------------------------------------
# Deterministic like federal, and honest about coverage: states with no wage
# income tax return $0 with a note; California is modeled precisely; any other
# state returns an explicit "not modeled" warning instead of a silent zero.

NO_WAGE_TAX_STATES = frozenset({"AK", "FL", "NH", "NV", "SD", "TN", "TX", "WA", "WY"})

# California 2025 FTB parameters — the latest the FTB has published (state
# years lag: 2026 CA tables appear in late 2026). Indexed 3.0% (CCPI June
# 2024 → June 2025). Single thresholds; married_joint doubles them.
_CA_SINGLE_BRACKETS: list[tuple[int | None, Decimal]] = [
    (11_079_00, Decimal("0.01")),
    (26_264_00, Decimal("0.02")),
    (41_452_00, Decimal("0.04")),
    (57_542_00, Decimal("0.06")),
    (72_724_00, Decimal("0.08")),
    (371_479_00, Decimal("0.093")),
    (445_771_00, Decimal("0.103")),
    (742_953_00, Decimal("0.113")),
    (None, Decimal("0.123")),
]
_CA_STANDARD_DEDUCTION = {"single": 5_706_00, "married_joint": 11_412_00}
_CA_MENTAL_HEALTH_THRESHOLD = 1_000_000_00  # extra 1% on taxable income above


def _bracket_tax(taxable: int, brackets: list[tuple[int | None, Decimal]]) -> Decimal:
    tax = Decimal(0)
    lower = 0
    for upper, rate in brackets:
        span_top = taxable if upper is None else min(taxable, upper)
        if span_top > lower:
            tax += (Decimal(span_top) - Decimal(lower)) * rate
        if upper is None or taxable <= upper:
            break
        lower = upper
    return tax


def _california_tax_minor(gross_minor: int, filing_status: str) -> tuple[int, list[str]]:
    notes = [
        "California state tax uses 2025 FTB brackets and standard deduction "
        "(the latest the FTB has published); CA SDI (1.2% of all wages, no "
        "cap) is not modeled."
    ]
    if filing_status == "married_joint":
        deduction = _CA_STANDARD_DEDUCTION["married_joint"]
        brackets = [
            (None if upper is None else upper * 2, rate)
            for upper, rate in _CA_SINGLE_BRACKETS
        ]
    else:
        deduction = _CA_STANDARD_DEDUCTION["single"]
        brackets = _CA_SINGLE_BRACKETS
        if filing_status == "head_of_household":
            notes.append(
                "California head-of-household brackets are approximated with "
                "single brackets (slightly conservative)."
            )
    taxable = max(0, gross_minor - deduction)
    tax = _bracket_tax(taxable, brackets)
    if taxable > _CA_MENTAL_HEALTH_THRESHOLD:
        tax += (Decimal(taxable) - Decimal(_CA_MENTAL_HEALTH_THRESHOLD)) * Decimal("0.01")
    return _to_minor(tax), notes


def _state_tax_minor(
    gross_minor: int, filing_status: str, state: str | None
) -> tuple[int | None, list[str]]:
    """(state tax in minor units or None when unmodeled, notes for the user)."""
    if state is None:
        return None, [
            "No state set — state income tax is NOT included. Set your state "
            "on the Income & Tax page for a more accurate estimate."
        ]
    state = state.upper()
    if state in NO_WAGE_TAX_STATES:
        return 0, [f"{state} has no state income tax on wages."]
    if state == "CA":
        return _california_tax_minor(gross_minor, filing_status)
    return None, [
        f"State income tax for {state} is not modeled yet — the estimate "
        "covers federal + FICA only and is therefore LOW."
    ]


_ASSUMPTIONS = [
    f"US federal estimate for tax year {TAX_YEAR} (IRS Rev. Proc. 2025-32 parameters).",
    "Standard deduction only — no itemizing, no credits, no other income adjustments.",
    "Employee-side FICA (6.2% Social Security to the wage base, 1.45% Medicare "
    "plus 0.9% additional over the threshold); self-employment tax not modeled.",
    "Local/city income taxes are not modeled.",
]


def _validate(filing_status: str) -> None:
    if filing_status not in FILING_STATUSES:
        raise ValueError(f"filing_status must be one of {FILING_STATUSES}")


def _staleness_notes(today: date | None) -> list[str]:
    """Self-enforcing yearly refresh: past TAX_YEAR every estimate says so."""
    current_year = (today or date.today()).year
    if current_year <= TAX_YEAR:
        return []
    return [
        f"STALE TAX PARAMETERS: this estimate uses tax-year {TAX_YEAR} law, "
        f"but it is now {current_year}. The yearly parameter refresh is due "
        "(docs/guides/tax-parameter-updates.md) — brackets, deductions, the "
        "Social Security wage base, and state tables have likely changed."
    ]


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


def _total_tax_minor(gross_minor: int, filing_status: str, state: str | None = None) -> int:
    state_tax, _notes = _state_tax_minor(gross_minor, filing_status, state)
    return (
        _federal_income_tax_minor(gross_minor, filing_status)
        + _fica_tax_minor(gross_minor, filing_status)
        + (state_tax or 0)
    )


def estimate_annual_tax(
    gross_annual: Money,
    filing_status: str,
    state: str | None = None,
    *,
    today: date | None = None,
) -> CalculationResult:
    """Federal income tax + employee FICA (+ modeled state tax) on annual GROSS income."""
    _validate(filing_status)
    currency = gross_annual.currency
    gross = max(0, gross_annual.amount_minor)
    federal = _federal_income_tax_minor(gross, filing_status)
    fica = _fica_tax_minor(gross, filing_status)
    state_tax, state_notes = _state_tax_minor(gross, filing_status, state)
    staleness = _staleness_notes(today)
    total = federal + fica + (state_tax or 0)
    effective_rate = round(total / gross, 4) if gross > 0 else 0.0
    return CalculationResult(
        calculation_type="annual_tax_estimate",
        version=CALCULATION_ENGINE_VERSION,
        inputs={
            "filing_status": filing_status,
            "tax_year": TAX_YEAR,
            "currency": currency,
            "state": state,
        },
        assumptions=[*_ASSUMPTIONS, *state_notes, *staleness],
        outputs={
            "gross_income": Money(gross, currency),
            "standard_deduction": Money(_STANDARD_DEDUCTION[filing_status], currency),
            "taxable_income": Money(
                max(0, gross - _STANDARD_DEDUCTION[filing_status]), currency
            ),
            "federal_income_tax": Money(federal, currency),
            "fica_tax": Money(fica, currency),
            "state_income_tax": Money(state_tax, currency) if state_tax is not None else None,
            "total_tax": Money(total, currency),
            "effective_rate": effective_rate,
        },
        warnings=list(staleness),
    )


def gross_up_from_net(
    net_annual: Money, filing_status: str, state: str | None = None
) -> CalculationResult:
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
        next_gross = net + _total_tax_minor(gross, filing_status, state)
        if abs(next_gross - gross) <= 1:
            gross = next_gross
            break
        gross = next_gross
    result = estimate_annual_tax(Money(gross, currency), filing_status, state)
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
            # The inner estimate's assumptions include the state-tax notes
            # and any parameter-staleness notice.
            *result.assumptions,
        ],
        outputs=outputs,
        warnings=list(result.warnings),
    )
