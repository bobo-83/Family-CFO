from __future__ import annotations

from dataclasses import dataclass

from family_cfo_financial_engine.money import Money
from family_cfo_financial_engine.results import CALCULATION_ENGINE_VERSION, CalculationResult

ASSET_ACCOUNT_TYPES = frozenset(
    {
        "checking",
        "savings",
        "brokerage",
        "retirement",
        "hsa",
        "529",
        "real_estate",
        "other_asset",
    }
)
LIABILITY_ACCOUNT_TYPES = frozenset(
    {
        "credit_card",
        "mortgage",
        "auto_loan",
        "student_loan",
        "other_liability",
    }
)
# A 401(k) loan is borrowed from — and repaid to — the household's own retirement,
# so it is net-worth-neutral: excluded from net worth entirely rather than shown as
# an external asset or liability. Its monthly repayment is still a cash-flow claim
# (handled in safe-to-spend), but the balance is a wash against retirement.
RETIREMENT_LOAN_TYPES = frozenset({"401k_loan"})


@dataclass(frozen=True, slots=True)
class AccountBalance:
    """An account balance input to net worth.

    ``balance`` is signed: positive for value the household owns, negative
    for amounts owed, matching the ``amount_minor`` convention in the
    domain model.
    """

    account_id: str
    account_type: str
    balance: Money


def calculate_net_worth(balances: list[AccountBalance], currency: str) -> CalculationResult:
    total = Money.zero(currency)
    asset_total = Money.zero(currency)
    liability_total = Money.zero(currency)
    warnings: list[str] = []

    for balance in balances:
        # A 401(k) loan is owed to yourself — net-worth-neutral, so it counts as
        # neither an asset nor a liability nor toward the total.
        if balance.account_type in RETIREMENT_LOAN_TYPES:
            continue
        total += balance.balance

        if balance.account_type in ASSET_ACCOUNT_TYPES:
            asset_total += balance.balance
        elif balance.account_type in LIABILITY_ACCOUNT_TYPES:
            liability_total += balance.balance
        else:
            warnings.append(
                f"account {balance.account_id!r} has unrecognized type "
                f"{balance.account_type!r}; included in net worth but not asset/liability subtotals"
            )

    return CalculationResult(
        calculation_type="net_worth",
        version=CALCULATION_ENGINE_VERSION,
        inputs={"account_count": len(balances), "currency": currency},
        assumptions=[
            "Account balances are signed: positive for assets, negative for liabilities.",
            "Net worth is the sum of all supplied account balances.",
        ],
        outputs={
            "net_worth": total,
            "asset_total": asset_total,
            "liability_total": liability_total,
        },
        warnings=warnings,
    )
