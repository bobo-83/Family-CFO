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

# Massachusetts 2026 DOR parameters (M81; 2026 Form 2-ES / mass.gov): flat 5%
# on wages, personal exemption per filing status, an up-to-$2,000-per-person
# deduction for FICA paid (Form 1 lines 11a/11b), and the Fair Share 4%
# surtax on taxable income above the annually indexed threshold.
_MA_RATE = Decimal("0.05")
_MA_PERSONAL_EXEMPTION = {
    "single": 4_400_00,
    "married_joint": 8_800_00,
    "head_of_household": 6_800_00,
}
_MA_FICA_DEDUCTION_PER_PERSON = 2_000_00
_MA_SURTAX_RATE = Decimal("0.04")
_MA_SURTAX_THRESHOLD = 1_107_750_00  # tax year 2026 (indexed annually)

# --- M82: every remaining income-tax state + DC -------------------------------
# Transcribed from the Tax Foundation's "2026 State Income Tax Rates and
# Brackets" compilation (the standard annual survey of all state codes).
# Approximate BY DESIGN: rates/brackets plus the basic standard deduction and
# personal exemption (or the state's flat personal CREDIT). Other credits,
# exemption phase-outs, recapture, and local/county income taxes are not
# modeled — each estimate says so. CA and MA above stay primary-source models.
#
# Entry shape: "single" = [(upper bound in minor units | None, rate)], lower
# bound of each bracket = previous upper bound. "married" = explicit schedule,
# or "double" (thresholds are 2x single), or absent (same as single).
# "ded" = (single, married_joint) pre-tax offset. "credit" = flat tax credit.
_STATE_TABLE: dict[str, dict] = {
    # Flat-rate states.
    "AZ": {"single": [(None, "0.025")], "ded": (8_350_00, 16_700_00)},
    "CO": {"single": [(None, "0.044")], "ded": (16_100_00, 32_200_00)},
    "GA": {"single": [(None, "0.0519")], "ded": (12_000_00, 24_000_00)},
    "ID": {"single": [(4_811_00, "0"), (None, "0.053")], "married": "double",
           "ded": (16_100_00, 32_200_00)},
    "IL": {"single": [(None, "0.0495")], "ded": (2_925_00, 5_850_00)},
    "IN": {"single": [(None, "0.0295")], "ded": (1_000_00, 2_000_00)},
    "IA": {"single": [(None, "0.038")], "ded": (16_100_00, 32_200_00),
           "credit": (40_00, 80_00)},
    "KY": {"single": [(None, "0.035")], "ded": (3_360_00, 3_360_00)},
    "LA": {"single": [(None, "0.03")], "ded": (12_875_00, 25_750_00)},
    "MI": {"single": [(None, "0.0425")], "ded": (5_900_00, 11_800_00)},
    "MS": {"single": [(10_000_00, "0"), (None, "0.04")],
           "ded": (8_300_00, 16_600_00)},
    "NC": {"single": [(None, "0.0399")], "ded": (12_750_00, 25_500_00)},
    "OH": {"single": [(26_050_00, "0"), (None, "0.0275")],
           "ded": (2_400_00, 4_800_00)},
    "PA": {"single": [(None, "0.0307")]},
    "UT": {"single": [(None, "0.045")], "credit": (966_00, 1_932_00)},
    # Graduated states + DC.
    "AL": {"single": [(500_00, "0.02"), (3_000_00, "0.04"), (None, "0.05")],
           "married": [(1_000_00, "0.02"), (6_000_00, "0.04"), (None, "0.05")],
           "ded": (4_500_00, 11_500_00)},
    "AR": {"single": [(4_600_00, "0.02"), (None, "0.039")],
           "ded": (2_470_00, 4_940_00), "credit": (29_00, 58_00)},
    "CT": {"single": [(10_000_00, "0.02"), (50_000_00, "0.045"),
                      (100_000_00, "0.055"), (200_000_00, "0.06"),
                      (250_000_00, "0.065"), (500_000_00, "0.069"),
                      (None, "0.0699")],
           "married": [(20_000_00, "0.02"), (100_000_00, "0.045"),
                       (200_000_00, "0.055"), (400_000_00, "0.06"),
                       (500_000_00, "0.065"), (1_000_000_00, "0.069"),
                       (None, "0.0699")]},
    "DE": {"single": [(2_000_00, "0"), (5_000_00, "0.022"), (10_000_00, "0.039"),
                      (20_000_00, "0.048"), (25_000_00, "0.052"),
                      (60_000_00, "0.0555"), (None, "0.066")],
           "ded": (3_250_00, 6_500_00), "credit": (110_00, 220_00)},
    "DC": {"single": [(10_000_00, "0.04"), (40_000_00, "0.06"),
                      (60_000_00, "0.065"), (250_000_00, "0.085"),
                      (500_000_00, "0.0925"), (1_000_000_00, "0.0975"),
                      (None, "0.1075")],
           "ded": (16_100_00, 32_200_00)},
    "HI": {"single": [(9_600_00, "0.014"), (14_400_00, "0.032"),
                      (19_200_00, "0.055"), (24_000_00, "0.064"),
                      (36_000_00, "0.068"), (48_000_00, "0.072"),
                      (125_000_00, "0.076"), (175_000_00, "0.079"),
                      (225_000_00, "0.0825"), (275_000_00, "0.09"),
                      (325_000_00, "0.10"), (None, "0.11")],
           "married": "double", "ded": (5_544_00, 11_088_00)},
    "KS": {"single": [(23_000_00, "0.052"), (None, "0.0558")],
           "married": [(46_000_00, "0.052"), (None, "0.0558")],
           "ded": (12_765_00, 26_560_00)},
    "ME": {"single": [(27_399_00, "0.058"), (64_849_00, "0.0675"), (None, "0.0715")],
           "married": [(54_849_00, "0.058"), (129_749_00, "0.0675"), (None, "0.0715")],
           "ded": (13_650_00, 27_300_00)},
    "MD": {"single": [(1_000_00, "0.02"), (2_000_00, "0.03"), (3_000_00, "0.04"),
                      (100_000_00, "0.0475"), (125_000_00, "0.05"),
                      (150_000_00, "0.0525"), (250_000_00, "0.055"),
                      (500_000_00, "0.0575"), (1_000_000_00, "0.0625"),
                      (None, "0.065")],
           "married": [(1_000_00, "0.02"), (2_000_00, "0.03"), (3_000_00, "0.04"),
                       (150_000_00, "0.0475"), (175_000_00, "0.05"),
                       (225_000_00, "0.0525"), (300_000_00, "0.055"),
                       (600_000_00, "0.0575"), (1_200_000_00, "0.0625"),
                       (None, "0.065")],
           "ded": (6_550_00, 13_100_00)},
    "MN": {"single": [(33_310_00, "0.0535"), (109_430_00, "0.068"),
                      (203_150_00, "0.0785"), (None, "0.0985")],
           "married": [(48_700_00, "0.0535"), (193_480_00, "0.068"),
                       (337_930_00, "0.0785"), (None, "0.0985")],
           "ded": (15_300_00, 30_600_00)},
    "MO": {"single": [(1_348_00, "0"), (2_696_00, "0.02"), (4_044_00, "0.025"),
                      (5_392_00, "0.03"), (6_740_00, "0.035"), (8_088_00, "0.04"),
                      (9_436_00, "0.045"), (None, "0.047")],
           "ded": (16_100_00, 32_200_00)},
    "MT": {"single": [(47_500_00, "0.047"), (None, "0.0565")],
           "married": [(95_000_00, "0.047"), (None, "0.0565")],
           "ded": (16_100_00, 32_200_00)},
    "NE": {"single": [(4_130_00, "0.0246"), (24_760_00, "0.0351"), (None, "0.0455")],
           "married": [(8_250_00, "0.0246"), (49_530_00, "0.0351"), (None, "0.0455")],
           "ded": (8_850_00, 17_700_00), "credit": (176_00, 352_00)},
    "NJ": {"single": [(20_000_00, "0.014"), (35_000_00, "0.0175"),
                      (40_000_00, "0.035"), (75_000_00, "0.0553"),
                      (500_000_00, "0.0637"), (1_000_000_00, "0.0897"),
                      (None, "0.1075")],
           "married": [(20_000_00, "0.014"), (50_000_00, "0.0175"),
                       (70_000_00, "0.0245"), (80_000_00, "0.035"),
                       (150_000_00, "0.0553"), (500_000_00, "0.0637"),
                       (1_000_000_00, "0.0897"), (None, "0.1075")],
           "ded": (1_000_00, 2_000_00)},
    "NM": {"single": [(5_500_00, "0.015"), (16_500_00, "0.032"),
                      (33_500_00, "0.043"), (66_500_00, "0.047"),
                      (210_000_00, "0.049"), (None, "0.059")],
           "married": [(8_000_00, "0.015"), (25_000_00, "0.032"),
                       (50_000_00, "0.043"), (100_000_00, "0.047"),
                       (315_000_00, "0.049"), (None, "0.059")],
           "ded": (16_100_00, 32_200_00)},
    "NY": {"single": [(8_500_00, "0.039"), (11_700_00, "0.044"),
                      (13_900_00, "0.0515"), (80_650_00, "0.054"),
                      (215_400_00, "0.059"), (1_077_550_00, "0.0685"),
                      (5_000_000_00, "0.0965"), (25_000_000_00, "0.103"),
                      (None, "0.109")],
           "married": [(17_150_00, "0.039"), (23_600_00, "0.044"),
                       (27_900_00, "0.0515"), (161_550_00, "0.054"),
                       (323_200_00, "0.059"), (2_155_350_00, "0.0685"),
                       (5_000_000_00, "0.0965"), (25_000_000_00, "0.103"),
                       (None, "0.109")],
           "ded": (8_000_00, 16_050_00)},
    "ND": {"single": [(48_475_00, "0"), (244_825_00, "0.0195"), (None, "0.025")],
           "married": [(80_975_00, "0"), (298_075_00, "0.0195"), (None, "0.025")],
           "ded": (16_100_00, 32_200_00)},
    "OK": {"single": [(3_750_00, "0"), (4_900_00, "0.025"), (7_200_00, "0.035"),
                      (None, "0.045")],
           "married": [(7_500_00, "0"), (9_800_00, "0.025"), (14_400_00, "0.035"),
                       (None, "0.045")],
           "ded": (7_350_00, 14_700_00)},
    "OR": {"single": [(4_550_00, "0.0475"), (11_400_00, "0.0675"),
                      (125_000_00, "0.0875"), (None, "0.099")],
           "married": [(9_100_00, "0.0475"), (22_800_00, "0.0675"),
                       (250_000_00, "0.0875"), (None, "0.099")],
           "ded": (2_910_00, 5_820_00), "credit": (256_00, 512_00)},
    "RI": {"single": [(82_050_00, "0.0375"), (186_450_00, "0.0475"),
                      (None, "0.0599")],
           "ded": (16_450_00, 32_900_00)},
    "SC": {"single": [(3_640_00, "0"), (18_230_00, "0.03"), (None, "0.06")],
           "ded": (8_350_00, 16_700_00)},
    "VT": {"single": [(49_400_00, "0.0335"), (119_700_00, "0.066"),
                      (249_700_00, "0.076"), (None, "0.0875")],
           "married": [(82_500_00, "0.0335"), (199_450_00, "0.066"),
                       (304_000_00, "0.076"), (None, "0.0875")],
           "ded": (12_950_00, 25_900_00)},
    "VA": {"single": [(3_000_00, "0.02"), (5_000_00, "0.03"), (17_000_00, "0.05"),
                      (None, "0.0575")],
           "ded": (9_680_00, 19_360_00)},
    "WV": {"single": [(10_000_00, "0.0222"), (25_000_00, "0.0296"),
                      (40_000_00, "0.0333"), (60_000_00, "0.0444"),
                      (None, "0.0482")],
           "ded": (2_000_00, 4_000_00)},
    "WI": {"single": [(15_110_00, "0.035"), (51_950_00, "0.044"),
                      (332_720_00, "0.053"), (None, "0.0765")],
           "married": [(20_150_00, "0.035"), (69_260_00, "0.044"),
                       (443_630_00, "0.053"), (None, "0.0765")],
           "ded": (14_660_00, 27_240_00)},
}

# States where the compilation-based estimate needs a louder caveat.
_STATE_EXTRA_NOTES: dict[str, str] = {
    "MD": "Maryland counties levy a MANDATORY local income tax "
          "(2.25%–3.2%) on top of this — the estimate is LOW.",
    "NY": "New York City / Yonkers city income tax and the high-income "
          "rate recapture are not modeled.",
    "OH": "Most Ohio municipalities levy their own income tax on top of "
          "this — the estimate is likely LOW.",
    "PA": "Pennsylvania local earned-income taxes (typically ~1%) are not "
          "modeled — the estimate is likely LOW.",
    "IN": "Indiana counties levy a mandatory local income tax on top of "
          "this — the estimate is LOW.",
    "CT": "Connecticut's personal exemption phases out with income and is "
          "NOT applied here (slightly conservative); the high-income tax "
          "recapture is not modeled.",
}


def _table_state_tax_minor(
    state: str, gross_minor: int, filing_status: str
) -> tuple[int, list[str]]:
    spec = _STATE_TABLE[state]
    notes = [
        f"{state} state tax uses the Tax Foundation {TAX_YEAR} compilation "
        "(rates, brackets, basic standard deduction/exemption or personal "
        "credit). Other credits, phase-outs, and local/county income taxes "
        "are not modeled — treat as approximate."
    ]
    married = filing_status == "married_joint"
    schedule = spec["single"]
    if married and "married" in spec:
        if spec["married"] == "double":
            schedule = [
                (None if upper is None else upper * 2, rate)
                for upper, rate in schedule
            ]
        else:
            schedule = spec["married"]
    if filing_status == "head_of_household":
        notes.append(
            f"{state} head-of-household brackets are approximated with "
            "single brackets."
        )
    index = 1 if married else 0
    deduction = spec.get("ded", (0, 0))[index]
    taxable = max(0, gross_minor - deduction)
    tax = _bracket_tax(taxable, [(u, Decimal(r)) for u, r in schedule])
    credit = spec.get("credit")
    if credit is not None:
        tax = max(Decimal(0), tax - Decimal(credit[index]))
    if state in _STATE_EXTRA_NOTES:
        notes.append(_STATE_EXTRA_NOTES[state])
    return _to_minor(tax), notes


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


def _massachusetts_tax_minor(gross_minor: int, filing_status: str) -> tuple[int, list[str]]:
    notes = [
        f"Massachusetts state tax uses {TAX_YEAR} DOR parameters: flat 5% on "
        "wages after the personal exemption and the up-to-$2,000-per-person "
        "FICA deduction (assumed maxed — true above ~$27k of wages per "
        f"earner), plus the 4% surtax on taxable income over "
        f"${_MA_SURTAX_THRESHOLD // 100:,}. MA PFML payroll contributions "
        "are not modeled."
    ]
    persons = 2 if filing_status == "married_joint" else 1
    deductions = (
        _MA_PERSONAL_EXEMPTION[filing_status] + persons * _MA_FICA_DEDUCTION_PER_PERSON
    )
    taxable = max(0, gross_minor - deductions)
    tax = Decimal(taxable) * _MA_RATE
    if taxable > _MA_SURTAX_THRESHOLD:
        tax += (Decimal(taxable) - Decimal(_MA_SURTAX_THRESHOLD)) * _MA_SURTAX_RATE
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
    if state == "MA":
        return _massachusetts_tax_minor(gross_minor, filing_status)
    if state in _STATE_TABLE:
        return _table_state_tax_minor(state, gross_minor, filing_status)
    # Only unknown codes land here now (every US state + DC is modeled).
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
