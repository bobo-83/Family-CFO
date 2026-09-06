import pytest
from sqlalchemy import select
from sqlalchemy.engine import Engine

from family_cfo_api import ai_tools, fixtures, models


def _execute(engine: Engine, name: str, args: dict):
    executor = ai_tools.build_executor(engine, fixtures.DEMO_HOUSEHOLD_ID, "USD")
    return executor(name, args)


def test_read_tool_returns_grounded_output_and_persists_calc(demo_engine: Engine) -> None:
    result = _execute(demo_engine, "get_net_worth", {})

    assert "outputs" in result
    assert result["outputs"]["net_worth"]["currency"] == "USD"
    assert result["calculation_ref"].startswith("financial_calculations:")

    with demo_engine.connect() as conn:
        rows = conn.execute(
            select(models.financial_calculations).where(
                models.financial_calculations.c.calculation_type == "net_worth"
            )
        ).all()
    assert len(rows) >= 1


def test_future_value_tool_computes_growth(demo_engine: Engine) -> None:
    result = _execute(
        demo_engine,
        "future_value",
        {"present_value_minor": 100_000, "annual_return_rate": 0.06, "years": 20},
    )

    # $1,000 at 6% for 20 years -> $3,207.14 (engine-verified).
    assert result["outputs"]["future_value"]["amount_minor"] == 320_714
    assert result["calculation_ref"].startswith("financial_calculations:")


def test_missing_required_argument_reports_missing_input(demo_engine: Engine) -> None:
    result = _execute(demo_engine, "future_value", {"annual_return_rate": 0.06, "years": 20})

    assert result["error"] == "missing_input"
    assert result["missing"] == "present_value"  # dollars, ADR 0063


def test_out_of_range_rate_reports_invalid_arguments(demo_engine: Engine) -> None:
    result = _execute(
        demo_engine,
        "future_value",
        {"present_value_minor": 1000, "annual_return_rate": 5, "years": 10},
    )

    assert result["error"] == "invalid_arguments"


def test_foreign_currency_is_rejected_for_single_currency_household(demo_engine: Engine) -> None:
    result = _execute(
        demo_engine,
        "project_purchase_impact",
        {"price_minor": 100_000, "currency": "EUR"},
    )

    assert result["error"] == "invalid_arguments"
    assert "USD" in result["detail"]


def test_retirement_requires_retirement_age_after_current_age(demo_engine: Engine) -> None:
    result = _execute(
        demo_engine,
        "project_retirement",
        {
            "current_age": 50,
            "retirement_age": 40,
            "current_savings_minor": 1000,
            "monthly_contribution_minor": 100,
            "annual_return_rate": 0.05,
        },
    )

    assert result["error"] == "invalid_arguments"


def test_retirement_grounds_savings_and_expenses_from_household_data(
    demo_engine: Engine,
) -> None:
    """The demo lesson: 'when can I retire?' must not ask for the 401k balance
    the Accounts tab shows. Ages alone suffice; savings default to retirement+HSA
    balances, expenses to 12x essentials, and every assumption is reported."""
    fixtures.seed_showcase_data(demo_engine)  # 401k (showcase) = $285,000

    result = _execute(
        demo_engine, "project_retirement", {"current_age": 35, "retirement_age": 60}
    )

    assert "error" not in result
    assumptions = result["grounded_defaults"]
    funded = assumptions["current_savings_from_accounts"]
    assert any("401k" in a["name"] for a in funded)
    assert sum(a["balance"]["amount_minor"] for a in funded) >= 28_500_000
    assert assumptions["monthly_contribution_assumed_zero"] is True
    assert assumptions["annual_return_rate_default"] == 0.05
    assert "essentials" in assumptions["annual_expenses_basis"]
    assert "state" in result["grounded_defaults_note"]


def test_retirement_explicit_args_override_grounding(demo_engine: Engine) -> None:
    result = _execute(
        demo_engine,
        "project_retirement",
        {
            "current_age": 35,
            "retirement_age": 60,
            "current_savings_minor": 5_000_000,
            "monthly_contribution_minor": 100_000,
            "annual_return_rate": 0.06,
            "annual_expenses_minor": 6_000_000,
        },
    )

    assert "error" not in result
    assert "grounded_defaults" not in result  # nothing was assumed


def test_grounding_rules_forbid_asking_for_retirement_balances() -> None:
    rules = ai_tools.GROUNDING_RULES
    assert "project_retirement" in rules
    assert "NEVER ask for retirement balances" in rules


def test_when_can_i_retire_solves_for_age_without_target(demo_engine: Engine) -> None:
    """'When can I retire?' is a question to ANSWER — current age suffices; the
    tool solves for the earliest age and grounds savings/expenses itself."""
    fixtures.seed_showcase_data(demo_engine)

    result = _execute(demo_engine, "when_can_i_retire", {"current_age": 43})

    assert "error" not in result
    outputs = result["outputs"]
    assert "earliest_retirement_age" in outputs
    assert outputs["required_balance"]["amount_minor"] > 0
    defaults = result["grounded_defaults"]
    assert any("401k" in a["name"] for a in defaults["current_savings_from_accounts"])
    assert defaults["monthly_contribution_assumed_zero"] is True


def test_when_can_i_retire_rules_forbid_asking_target_age() -> None:
    rules = ai_tools.GROUNDING_RULES
    assert "when_can_i_retire" in rules
    assert "never ask the user for a target retirement age" in rules


def test_unknown_tool_is_reported(demo_engine: Engine) -> None:
    result = _execute(demo_engine, "delete_everything", {})

    assert result["error"] == "unknown_tool"


def test_system_prompt_layers_persona_over_invariant_grounding_rules() -> None:
    from family_cfo_api.config import Settings

    playful = ai_tools.build_system_prompt(Settings(ai_tone="playful"))
    professional = ai_tools.build_system_prompt(Settings(ai_tone="professional"))

    # Persona differs...
    assert "cheeky" in playful and "cheeky" not in professional
    assert "no emoji" in professional
    # ...but the grounding rules are identical and complete in both tones.
    for prompt in (playful, professional):
        assert "ONLY the provided tools" in prompt
        assert "missing_input" in prompt
        assert "never include names, account details" in prompt
    # Unknown tone falls back to playful, never to an empty persona.
    assert "cheeky" in ai_tools.build_system_prompt(Settings(ai_tone="klingon"))


def test_grounded_values_include_rounded_variants() -> None:
    """A model saying "9.6 months" for a tool's 9.6470588 is honest rounding."""
    from family_cfo_ai_orchestrator import ToolCallingResult
    from family_cfo_ai_orchestrator.tool_calling import ToolCallRecord

    result = ToolCallingResult(
        answer="x",
        completed=True,
        tool_calls=[
            ToolCallRecord(
                name="get_emergency_fund",
                arguments={},
                result={"outputs": {"emergency_fund_months": 9.6470588}},
            )
        ],
    )
    values = ai_tools.grounded_values(result)
    assert {"9.6470588", "9.6", "9.65", "10"} <= values


def test_net_worth_tool_breaks_assets_into_spendability_categories(demo_engine: Engine) -> None:
    """M33: the model must see retirement money as not-spendable, separately."""
    result = _execute(demo_engine, "get_net_worth", {})

    breakdown = result["asset_breakdown"]
    assert "liquid" in breakdown  # demo household has checking/savings
    for category, money in breakdown.items():
        assert money["amount_minor"] >= 0, category
    assert "NOT available for purchases" in result["spendability_note"]
    assert "retirement" in result["spendability_note"]


# --- M122 (#130): the accounts behind the totals ---


def _seed_accounts_for_inventory(engine: Engine):
    """A household with one of each interesting shape: a designated emergency
    fund, an RSU-tagged brokerage, and a 401(k) loan."""
    from family_cfo_api import repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    brokerage = repository.create_account(engine, hh, "Vested RSUs", "brokerage", "USD")
    repository.record_account_balance(engine, brokerage.id, 2_500_000)
    repository.update_account(engine, hh, brokerage.id, rsu_ready_to_sell=True)

    retirement = repository.create_account(engine, hh, "401k", "retirement", "USD")
    repository.record_account_balance(engine, retirement.id, 12_000_000)

    loan = repository.create_account(engine, hh, "401k Loan", "401k_loan", "USD")
    repository.record_account_balance(engine, loan.id, -800_000)

    # M36: the demo savings account IS the emergency fund.
    savings = next(b for b in repository.list_account_balances(engine, hh) if b.name == "Savings")
    repository.update_account(engine, hh, savings.account_id, emergency_fund_percent=100.0)
    return {"brokerage": brokerage, "loan": loan, "savings": savings}


def _seed_foreign_currency_account(engine: Engine):
    """POST /accounts accepts any ISO code without comparing it to the household
    base currency, so an account the family can see may sit outside it."""
    from family_cfo_api import repository

    euro = repository.create_account(engine, fixtures.DEMO_HOUSEHOLD_ID, "Euro Savings", "savings", "EUR")
    repository.record_account_balance(engine, euro.id, 400_000)
    return euro


def test_accounts_tool_itemises_every_account_behind_the_totals(demo_engine: Engine) -> None:
    """#130: asked which accounts make up a total, the advisor must be able to
    answer from a tool instead of sending the family to look it up elsewhere."""
    _seed_accounts_for_inventory(demo_engine)

    result = _execute(demo_engine, "get_accounts", {})

    by_name = {a["name"]: a for a in result["accounts"]}
    assert {"Checking", "Savings", "Mortgage", "401k", "Vested RSUs"} <= set(by_name)
    assert result["account_count"] == len(result["accounts"])

    checking = by_name["Checking"]
    assert checking["type"] == "checking"
    assert checking["category"] == "liquid"
    assert checking["is_liability"] is False
    assert checking["balance"]["amount_minor"] == 500_000
    assert checking["balance"]["currency"] == "USD"
    assert "5,000.00" in checking["balance"]["display"]

    # M33 categories travel with each row, so a balance can never be read as
    # spendable just because the model can see it.
    assert by_name["401k"]["category"] == "retirement"
    assert by_name["Vested RSUs"]["category"] == "investments"


def test_accounts_tool_marks_liabilities_including_a_401k_loan(demo_engine: Engine) -> None:
    """Liabilities have no spendability category by design; they get their own,
    and their balances stay signed exactly as the app records them."""
    _seed_accounts_for_inventory(demo_engine)

    by_name = {a["name"]: a for a in _execute(demo_engine, "get_accounts", {})["accounts"]}

    for name in ("Mortgage", "401k Loan"):
        assert by_name[name]["category"] == "debts", name
        assert by_name[name]["is_liability"] is True, name
        assert by_name[name]["balance"]["amount_minor"] < 0, name
    # get_debt_outlook stays the authority on the positive amount owed and terms.
    assert "get_debt_outlook" in _execute(demo_engine, "get_accounts", {})["note"]


def test_accounts_tool_does_not_call_an_overpaid_card_a_debt(demo_engine: Engine) -> None:
    """A liability's sign is a reading of the balance, not a property of the
    account type: an overpaid or refunded card is a credit, and the note must
    not tell the model every liability row is negative."""
    from family_cfo_api import repository

    card = repository.create_account(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "Overpaid Card", "credit_card", "USD"
    )
    repository.record_account_balance(demo_engine, card.id, 7_500)

    result = _execute(demo_engine, "get_accounts", {})
    row = next(a for a in result["accounts"] if a["name"] == "Overpaid Card")

    assert row["is_liability"] is True
    assert row["category"] == "debts"
    # Listed as recorded, not flipped or hidden — and explained, so a credit is
    # never reported as money owed.
    assert row["balance"]["amount_minor"] == 7_500
    assert "credit" in result["note"]
    assert "never report it as a debt" in result["note"]


def test_accounts_tool_surfaces_emergency_reservation_and_rsu_flag(demo_engine: Engine) -> None:
    """The per-account designations the record already holds (M36, RSU tag)."""
    _seed_accounts_for_inventory(demo_engine)

    by_name = {a["name"]: a for a in _execute(demo_engine, "get_accounts", {})["accounts"]}

    # 100% of the $15,000 savings balance is designated emergency money.
    assert by_name["Savings"]["emergency_fund_reserved"]["amount_minor"] == 1_500_000
    assert by_name["Vested RSUs"]["rsu_ready_to_sell"] is True
    # An undesignated account says so with null, not a zero reservation.
    assert by_name["Checking"]["emergency_fund_reserved"] is None
    assert by_name["Checking"]["rsu_ready_to_sell"] is False


def test_accounts_tool_lists_foreign_currency_accounts_rather_than_dropping_them(
    demo_engine: Engine,
) -> None:
    """An inventory that silently omits an account the family can see on screen
    recreates the very bug this tool fixes — flag it, never hide it."""
    _seed_accounts_for_inventory(demo_engine)
    _seed_foreign_currency_account(demo_engine)

    result = _execute(demo_engine, "get_accounts", {})
    by_name = {a["name"]: a for a in result["accounts"]}

    euro = by_name["Euro Savings"]
    assert euro["balance"]["currency"] == "EUR"
    assert euro["balance"]["amount_minor"] == 400_000
    assert euro["matches_base_currency"] is False
    assert by_name["Checking"]["matches_base_currency"] is True
    assert result["accounts_outside_base_currency"] == 1


def test_accounts_flag_claims_currency_match_not_membership_of_a_total(
    demo_engine: Engine,
) -> None:
    """The flag tests the currency and nothing else. A base-currency 401(k) loan
    matches, yet net worth skips retirement loans entirely and safe-to-spend
    counts only checking and savings — so the payload must not let the model read
    a match as "this is in that total"."""
    _seed_accounts_for_inventory(demo_engine)

    result = _execute(demo_engine, "get_accounts", {})
    by_name = {a["name"]: a for a in result["accounts"]}

    assert by_name["401k Loan"]["matches_base_currency"] is True
    assert "included_in_base_currency_totals" not in by_name["401k Loan"]
    assert "does NOT mean a given total includes the account" in result["note"]
    assert "401(k) loans" in result["note"]


def test_accounts_tool_marks_itself_current_only(demo_engine: Engine) -> None:
    """`get_net_worth(month=...)` answers for a past month; this tool always reads
    today's balances. Without an as-of marker the advisor can present today's
    accounts as the components of a historical total."""
    _seed_accounts_for_inventory(demo_engine)

    result = _execute(demo_engine, "get_accounts", {})
    assert result["as_of"] == "current"
    assert "TODAY" in result["note"]
    assert "get_net_worth(month=...)" in result["note"]

    historical = _execute(demo_engine, "get_net_worth", {"month": "2026-01"})
    assert "TODAY's accounts" in historical["note"]
    assert "Do not itemise this total" in historical["note"]

    spec = next(t for t in ai_tools.build_tools() if t.name == "get_accounts")
    assert "CURRENT balances only" in spec.description
    net_worth_spec = next(t for t in ai_tools.build_tools() if t.name == "get_net_worth")
    assert "cannot break down a past month" in net_worth_spec.description


def test_accounts_tool_routes_spending_and_debt_questions_elsewhere(demo_engine: Engine) -> None:
    """The payload must carry the guardrail, not just the data: per-account
    balances are exactly the input that invites re-deriving a spendable total."""
    result = _execute(demo_engine, "get_accounts", {})

    note = result["note"] + result["spendability_note"]
    assert "get_safe_to_spend" in note
    assert "get_debt_outlook" in note
    assert "NOT available for purchases" in result["spendability_note"]


def test_net_worth_keeps_its_categories_and_points_at_the_accounts_tool(
    demo_engine: Engine,
) -> None:
    """Regression: M33's guarantee survives the addition — the breakdown and its
    non-spendability warning are unchanged, and affordability still routes to
    get_safe_to_spend rather than inviting the model to subtract."""
    _seed_accounts_for_inventory(demo_engine)

    result = _execute(demo_engine, "get_net_worth", {})

    breakdown = result["asset_breakdown"]
    assert breakdown["liquid"]["amount_minor"] == 500_000 + 1_500_000
    assert breakdown["retirement"]["amount_minor"] == 12_000_000
    assert breakdown["investments"]["amount_minor"] == 2_500_000
    note = result["spendability_note"]
    assert "NOT available for purchases" in note and "retirement" in note
    # The old note told the model to subtract the emergency fund itself, which
    # the invariant rules forbid; affordability comes from one tool only.
    assert "get_safe_to_spend" in note
    assert "subtract it from liquid funds" not in note
    assert "get_accounts" in result["accounts_note"]


@pytest.mark.anyio
async def test_accounts_tool_and_accounts_endpoint_project_the_same_assembler(
    demo_engine: Engine, demo_client, demo_token
) -> None:
    """The Accounts tab and the advisor must never name different accounts.

    Both read `finance_service.list_account_views`; this pins the shared fields —
    including the preferred real institution over the generic connection name,
    the last sync time, the emergency-fund reservation and the RSU flag — so a
    change to one surface cannot drift from the other. Rows are compared as a
    sorted sequence rather than keyed by name, so two accounts sharing a name
    cannot hide a missing or mismatched row.
    """
    from datetime import datetime

    from family_cfo_api import repository

    _seed_accounts_for_inventory(demo_engine)
    _seed_foreign_currency_account(demo_engine)

    hh = fixtures.DEMO_HOUSEHOLD_ID
    connection = repository.create_institution_connection(
        demo_engine, hh, "simplefin", "SimpleFin (multiple banks)", "enc"
    )
    synced_id = repository.get_or_create_connection_account(
        demo_engine,
        hh,
        connection.id,
        external_account_id="ext-1",
        name="Brokerage (synced)",
        currency="USD",
        account_type="brokerage",
        institution="Charles Schwab",
    )
    repository.record_account_balance(demo_engine, synced_id, 100_000)
    # A real sync stamps the connection; both surfaces must report the same one.
    repository.record_connection_sync(demo_engine, connection.id, None)

    listed = await demo_client.get(
        "/api/v1/accounts", headers={"Authorization": f"Bearer {demo_token}"}
    )
    assert listed.status_code == 200
    def _stamp(value: str | None) -> datetime | None:
        """One datetime from either wire form (the endpoint may render UTC as Z)."""
        return None if value is None else datetime.fromisoformat(value)

    def _row(account: dict) -> tuple:
        return (
            account["name"],
            account["type"],
            account["balance"]["amount_minor"],
            account["balance"]["currency"],
            account["institution"],
            _stamp(account["last_synced_at"]),
            (account["emergency_fund_reserved"] or {}).get("amount_minor"),
            account["rsu_ready_to_sell"],
        )

    from_endpoint = sorted(_row(a) for a in listed.json()["accounts"])
    from_tool = sorted(_row(a) for a in _execute(demo_engine, "get_accounts", {})["accounts"])

    assert from_tool == from_endpoint
    synced = next(row for row in from_tool if row[0] == "Brokerage (synced)")
    # The provider's own org name wins over the generic connection display name.
    assert synced[4] == "Charles Schwab"
    # A synced account carries its "as of", a manual one has none.
    assert synced[5] is not None
    assert next(row for row in from_tool if row[0] == "Checking")[5] is None
    assert next(row for row in from_tool if row[0] == "Euro Savings")[3] == "EUR"


def test_accounts_tool_is_advertised_and_dispatches_by_name(demo_engine: Engine) -> None:
    names = [tool.name for tool in ai_tools.build_tools()]

    assert "get_accounts" in names
    spec = next(t for t in ai_tools.build_tools() if t.name == "get_accounts")
    assert "get_safe_to_spend" in spec.description
    assert _execute(demo_engine, "get_accounts", {})["accounts"]


def test_per_account_balances_are_quotable_by_the_grounding_guardrail(
    demo_engine: Engine,
) -> None:
    """The point of the issue: itemised balances become quotable, so naming an
    account's balance in an answer no longer trips the invented-figure check."""
    from family_cfo_ai_orchestrator import ToolCallingResult
    from family_cfo_ai_orchestrator.tool_calling import ToolCallRecord

    _seed_accounts_for_inventory(demo_engine)
    payload = _execute(demo_engine, "get_accounts", {})

    values = ai_tools.grounded_values(
        ToolCallingResult(
            answer="x",
            completed=True,
            tool_calls=[
                ToolCallRecord(name="get_accounts", arguments={}, result=payload)
            ],
        )
    )
    assert "120000.00" in values  # the 401k's $120,000, as the display shows it
    assert "25000.00" in values  # the vested RSUs
    # ...but the cents twin of the same figure is NOT quotable: grounding it
    # would let "$12,000,000" pass for a $120,000 account, a hundredfold
    # overstatement tracing to nothing the family holds.
    assert "12000000" not in values
    assert "2500000" not in values


def test_minor_units_never_ground_a_hundredfold_overstatement() -> None:
    """Every money figure ships as cents + display; only the display grounds."""
    from family_cfo_ai_orchestrator import ToolCallingResult
    from family_cfo_ai_orchestrator.tool_calling import ToolCallRecord

    values = ai_tools.grounded_values(
        ToolCallingResult(
            answer="x",
            completed=True,
            tool_calls=[
                ToolCallRecord(
                    name="get_net_worth",
                    arguments={},
                    result={
                        "net_worth": {
                            "amount_minor": 4_200_000,
                            "currency": "USD",
                            "display": "$42,000.00",
                        },
                        "months_of_runway": 6,
                    },
                )
            ],
        )
    )

    assert {"42000.00", "42000"} <= values
    assert "4200000" not in values
    # Plain integers that are not a money twin still ground normally.
    assert "6" in values


def _money_rows(value) -> list[tuple[int, str]]:
    """Every serialized Money in a payload, as (amount_minor, display)."""
    rows: list[tuple[int, str]] = []
    if isinstance(value, dict):
        if "amount_minor" in value and "display" in value:
            rows.append((value["amount_minor"], value["display"]))
        for item in value.values():
            rows.extend(_money_rows(item))
    elif isinstance(value, list):
        for item in value:
            rows.extend(_money_rows(item))
    return rows


# Every tool that returns money, with arguments that reach a real payload on the
# demo household. Dropping the minor-unit twin must not cost ANY of them a
# figure the model is meant to quote.
_MONEY_TOOL_CALLS = [
    ("get_net_worth", {}),
    ("get_accounts", {}),
    ("get_emergency_fund", {}),
    ("get_safe_to_spend", {}),
    ("get_debt_outlook", {}),
    ("get_debt_history", {}),
    ("get_income_and_tax", {}),
    ("get_bills", {}),
    ("get_budgets", {}),
    ("get_savings_contributions", {}),
    ("get_spending_insights", {}),
    ("get_spending_by_category", {}),
    ("find_savings", {}),
    ("project_purchase_impact", {"price": 1200.0}),
    ("future_value", {"present_value": 10_000.0, "annual_return_rate": 0.06, "years": 20}),
    ("when_can_i_retire", {"current_age": 43}),
    ("project_retirement", {"current_age": 43, "retirement_age": 65}),
    (
        "debt_payoff",
        {"balance": 5_000.0, "annual_interest_rate": 0.199, "minimum_payment": 150.0},
    ),
]


def _seed_money_for_every_tool(engine: Engine) -> None:
    """Enough of a household that every money tool returns real figures: the
    demo mortgage has no terms (so `get_debt_outlook` itemises nothing) and no
    envelope exists (so `get_budgets` is empty)."""
    from family_cfo_api import repository

    _seed_accounts_for_inventory(engine)
    hh = fixtures.DEMO_HOUSEHOLD_ID
    mortgage = next(b for b in repository.list_account_balances(engine, hh) if b.name == "Mortgage")
    repository.update_account(
        engine,
        hh,
        mortgage.account_id,
        annual_interest_rate=0.0625,
        minimum_payment_minor=180_000,
    )
    category = repository.create_category(engine, hh, "Groceries Envelope")
    repository.create_budget(
        engine, household_id=hh, category_id=category.id, limit_minor=50_000, currency="USD"
    )


@pytest.mark.parametrize(("tool", "args"), _MONEY_TOOL_CALLS)
def test_dropping_minor_units_costs_no_tool_a_quotable_figure(
    demo_engine: Engine, tool: str, args: dict
) -> None:
    """The guardrail change is subtractive, so the risk is a TRUE answer failing
    it and falling back to the deterministic snapshot. Sweep every money tool:
    each displayed amount must still ground, in the forms a model actually says
    ($5,000.00 written as "5,000.00", "5000.00" or "5,000")."""
    from family_cfo_ai_orchestrator import ToolCallingResult
    from family_cfo_ai_orchestrator.guardrails import extract_numbers
    from family_cfo_ai_orchestrator.tool_calling import ToolCallRecord

    _seed_money_for_every_tool(demo_engine)
    payload = _execute(demo_engine, tool, args)
    assert "error" not in payload, payload

    values = ai_tools.grounded_values(
        ToolCallingResult(
            answer="x",
            completed=True,
            tool_calls=[ToolCallRecord(name=tool, arguments=args, result=payload)],
        )
    )

    rows = _money_rows(payload)
    assert rows, f"{tool} returned no money to check"
    for minor, display in rows:
        for spoken in extract_numbers(display):
            assert spoken in values, f"{tool}: {display} lost its grounding"
        major = abs(minor) / 100
        assert f"{major:.2f}" in values, f"{tool}: {display}"
        if major == int(major):
            # "$5,000.00" read aloud as "5,000".
            assert str(int(major)) in values, f"{tool}: {display}"


def test_no_result_field_ships_a_bare_minor_unit_amount(demo_engine: Engine) -> None:
    """Every money figure must leave a tool through `_money_out`. A raw
    `*_minor` int beside a real holding is what lets an answer overstate it a
    hundredfold, and the filter can only protect what it can recognise."""
    import json
    import re

    _seed_money_for_every_tool(demo_engine)
    for tool, args in _MONEY_TOOL_CALLS:
        payload = _execute(demo_engine, tool, args)
        bare = re.findall(r'"(\w*_minor)":', json.dumps(payload, default=str))
        assert set(bare) <= {"amount_minor"}, f"{tool} ships raw minor units: {set(bare)}"


def test_grounding_filter_drops_minor_units_and_household_text() -> None:
    """Results lose every minor-unit field (a new one fails closed rather than
    reopening the hole) and every household-supplied identity string; arguments
    keep their minor units, because `_money_arg` still accepts the legacy
    `<field>_minor` form and echoing the user's own figure is legitimate."""
    payload = {
        "outputs": {
            "safe_to_spend": {"amount_minor": 250_000, "currency": "USD", "display": "$2,500.00"}
        },
        "vested_rsus_ready_to_sell_not_cash": [
            {"account": "Vested RSUs 9876", "value": {"amount_minor": 2_500_000, "display": "$25,000.00"}}
        ],
        "warnings": ["one debt has no recorded minimum payment"],
        "months_of_runway": 6,
    }

    filtered = ai_tools._groundable(payload, drop_minor_units=True)

    assert filtered["outputs"]["safe_to_spend"] == {"currency": "USD", "display": "$2,500.00"}
    rsu = filtered["vested_rsus_ready_to_sell_not_cash"][0]
    assert "account" not in rsu  # the name's digits are an identifier, not money
    assert rsu["value"] == {"display": "$25,000.00"}
    assert filtered["warnings"] == payload["warnings"]  # our own text still grounds
    assert filtered["months_of_runway"] == 6
    # Non-destructive: the caller's payload is untouched.
    assert payload["outputs"]["safe_to_spend"]["amount_minor"] == 250_000

    args = ai_tools._groundable(
        {"present_value_minor": 100_000, "query": "roof costs 9876"}, drop_minor_units=False
    )
    assert args["present_value_minor"] == 100_000
    assert "query" not in args  # a number the model typed is not a fact


def test_digits_in_an_account_name_are_not_a_quotable_figure(demo_engine: Engine) -> None:
    """Last-four identifiers are everywhere in account names. Grounding them let
    an invented figure that happened to match the digits pass the guardrail."""
    from family_cfo_ai_orchestrator import ToolCallingResult
    from family_cfo_ai_orchestrator.guardrails import validate_recommendation
    from family_cfo_ai_orchestrator.tool_calling import ToolCallRecord

    from family_cfo_api import repository

    account = repository.create_account(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "Fidelity Brokerage 9876", "brokerage", "USD"
    )
    repository.record_account_balance(demo_engine, account.id, 10_000)  # USD 100.00

    payload = _execute(demo_engine, "get_accounts", {})
    assert any(a["name"] == "Fidelity Brokerage 9876" for a in payload["accounts"])

    values = ai_tools.grounded_values(
        ToolCallingResult(
            answer="x",
            completed=True,
            tool_calls=[ToolCallRecord(name="get_accounts", arguments={}, result=payload)],
        )
    )

    assert "9876" not in values
    assert "9876.00" not in values
    assert not validate_recommendation("That brokerage holds USD 9,876.00 today.", values).passed
    # The real balance is still quotable, so the answer that IS true still passes.
    assert validate_recommendation("That brokerage holds USD 100.00 today.", values).passed


def test_naming_an_account_type_out_loud_is_not_a_violation(demo_engine: Engine) -> None:
    """Account names used to ground their own digits as a side effect, so a
    household with a "401k" account made 401 quotable. Now that names ground
    nothing, saying "401k" or "529 plan" must not fail the guardrail — otherwise
    the fix trades an invented-figure hole for a mute advisor."""
    from family_cfo_ai_orchestrator import ToolCallingResult
    from family_cfo_ai_orchestrator.guardrails import validate_recommendation
    from family_cfo_ai_orchestrator.tool_calling import ToolCallRecord

    fixtures.seed_showcase_data(demo_engine)
    payload = _execute(demo_engine, "when_can_i_retire", {"current_age": 43})
    values = ai_tools.grounded_values(
        ToolCallingResult(
            answer="x",
            completed=True,
            tool_calls=[ToolCallRecord(name="when_can_i_retire", arguments={}, result=payload)],
        )
    )

    balance = payload["grounded_defaults"]["current_savings_from_accounts"][0]["balance"]
    answer = f"Your 401k holds {balance['display']}, and the 529 plan stays untouched."
    assert validate_recommendation(answer, values).passed


def test_public_prices_found_by_web_search_stay_quotable() -> None:
    """The text filter must not reach `web_search`: a price the model found on
    the web lives in the snippet, and quoting it is the point of the tool."""
    from family_cfo_ai_orchestrator import ToolCallingResult
    from family_cfo_ai_orchestrator.tool_calling import ToolCallRecord

    values = ai_tools.grounded_values(
        ToolCallingResult(
            answer="x",
            completed=True,
            tool_calls=[
                ToolCallRecord(
                    name="web_search",
                    arguments={"query": "average cost of a new roof"},
                    result={
                        "results": [
                            {"title": "Roof costs", "snippet": "A new asphalt roof runs 12,500.00"}
                        ]
                    },
                )
            ],
        )
    )

    assert "12500.00" in values


# --- M64: income/tax, bills, budgets, spending tools ---


def _seed_income(engine: Engine) -> None:
    from datetime import date, timedelta

    from family_cfo_api import repository

    account = repository.create_account(
        engine, fixtures.DEMO_HOUSEHOLD_ID, name="Pay Checking", account_type="checking",
        currency="USD",
    )
    today = date.today()
    for i in range(4):
        repository.create_transaction(
            engine,
            household_id=fixtures.DEMO_HOUSEHOLD_ID,
            account_id=account.id,
            occurred_at=today - timedelta(days=14 * (4 - i)),
            amount_minor=461_538,
            currency="USD",
            merchant="ACME CORP PAYROLL",
            description=None,
            import_source=None,
            import_id=None,
            review_state="reviewed",
        )


def test_income_and_tax_tool_reports_sources_and_estimate(demo_engine: Engine) -> None:
    _seed_income(demo_engine)

    result = _execute(demo_engine, "get_income_and_tax", {})

    assert result["income_sources"][0]["name"] == "ACME CORP PAYROLL"
    assert result["income_sources"][0]["frequency"] == "biweekly"
    assert result["annual_income_detected"]["amount_minor"] == 4 * 461_538
    tax = result["tax_estimate"]
    assert tax["tax_year"] == 2026
    assert tax["estimated_total_tax"]["amount_minor"] > 0
    assert tax["estimated_gross_income"]["amount_minor"] > 4 * 461_538
    # Take-home is gross minus tax — the figure the advisor must quote for pay,
    # not the (undercounting) detected-deposit average.
    take_home = result["take_home"]["annual"]["amount_minor"]
    assert take_home == tax["estimated_gross_income"]["amount_minor"] - tax["estimated_total_tax"]["amount_minor"]
    assert result["take_home"]["monthly"]["amount_minor"] == round(take_home / 12)
    assert "undercount" in result["income_basis_note"].lower()
    assert any("state income tax is NOT included" in a for a in result["assumptions"])
    # Partial history is disclosed to the model too.
    assert any("not a full year" in w for w in result["warnings"])


def test_income_tool_month_mode_lists_the_individual_deposits(demo_engine: Engine) -> None:
    """User report 2026-07-25: asked "what made up my income in May?", the
    advisor could only quote the aggregate. Month mode must carry the rows."""
    from datetime import date, timedelta

    _seed_income(demo_engine)
    payday = date.today() - timedelta(days=14)
    month = payday.strftime("%Y-%m")

    result = _execute(demo_engine, "get_income_and_tax", {"month": month})

    deposits = result["deposits"]
    assert deposits, "the seeded paycheck must appear as an individual deposit"
    assert any(
        d["source"] == "ACME CORP PAYROLL"
        and d["account"] == "Pay Checking"
        and d["amount"]["amount_minor"] == 461_538
        and d["date"] == payday.isoformat()
        for d in deposits
    )
    # The aggregate equals (at least) the listed rows — chart-consistent (ADR 0066).
    assert result["income_received"]["amount_minor"] >= sum(
        d["amount"]["amount_minor"] for d in deposits
    )
    assert "deposits" in result["note"]


def test_income_tool_carries_structured_earner_and_w2(demo_engine: Engine) -> None:
    """M76 follow-up: W2 actuals reach the model as data, not just a prose line."""
    from datetime import date, timedelta

    from family_cfo_api import repository

    repository.create_income_profile(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        label="Alex",
        base_salary_minor=20_000_000,
        rsu_annual_minor=16_000_000,
        rsu_frequency="quarterly",
        rsu_next_vest_date=date.today() + timedelta(days=30),
        bonus_percent=25.0,
        bonus_month=12,
        w2_year=2025,
        w2_wages_minor=38_541_260,
        w2_withheld_minor=7_890_315,
    )

    result = _execute(demo_engine, "get_income_and_tax", {})

    earner = result["compensation_profile"]["earners"][0]
    assert earner["label"] == "Alex"
    assert earner["rsu_next_vest_date"] == (date.today() + timedelta(days=30)).isoformat()
    assert earner["bonus_month"] == 12
    w2 = earner["last_year_w2"]
    assert w2["year"] == 2025
    assert w2["box1_wages"]["amount_minor"] == 38_541_260
    assert w2["box2_federal_withheld"]["amount_minor"] == 7_890_315
    # The effective-rate calibration line rides along in the assumptions too.
    assert any("W2" in a for a in result["assumptions"])
    # M79: the model is told profile amounts and vest events are pre-tax.
    assert "PRE-TAX" in result["compensation_profile"]["note"]


def test_bills_tool_lists_bills_and_upcoming(demo_engine: Engine) -> None:
    from datetime import date, timedelta

    from family_cfo_api import repository

    repository.create_bill(
        engine=demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        name="Netflix",
        amount_minor=1_549,
        currency="USD",
        frequency="monthly",
        account_id=None,
        next_due_date=date.today() + timedelta(days=5),
    )

    result = _execute(demo_engine, "get_bills", {})

    assert [b["name"] for b in result["bills"]].count("Netflix") == 1
    due = [b for b in result["due_within_14_days"] if b["name"] == "Netflix"]
    assert due and due[0]["days_until"] == 5
    assert due[0]["amount"]["display"] == "USD 15.49"


def test_budgets_tool_reports_envelope_progress(demo_engine: Engine) -> None:
    from family_cfo_api import repository

    category = repository.create_category(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "Groceries Envelope")
    repository.create_budget(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        category_id=category.id,
        limit_minor=50_000,
        currency="USD",
    )

    result = _execute(demo_engine, "get_budgets", {})

    envelope = result["month_budgets"][0]
    assert envelope["category"] == "Groceries Envelope"
    assert envelope["limit"]["amount_minor"] == 50_000
    assert envelope["status"] in ("under", "warning", "over")


def test_spending_insights_tool_reports_month_comparison(demo_engine: Engine) -> None:
    result = _execute(demo_engine, "get_spending_insights", {})

    assert "month_to_date_spending" in result
    assert "same_window_last_month" in result
    assert isinstance(result["top_merchants"], list)


def test_safe_to_spend_tool_is_grounded_and_nets_out_obligations(demo_engine: Engine) -> None:
    result = _execute(demo_engine, "get_safe_to_spend", {})

    outputs = result["outputs"]
    assert outputs["safe_to_spend"]["currency"] == "USD"
    assert outputs["bills_due"]["amount_minor"] > 0
    assert result["calculation_ref"].startswith("financial_calculations:")

    liquid = outputs["liquid_balance"]["amount_minor"]
    reserved = outputs["emergency_fund_reserved"]["amount_minor"]
    bills = outputs["bills_due"]["amount_minor"]
    debt = outputs["minimum_debt_payments"]["amount_minor"]
    assert outputs["safe_to_spend"]["amount_minor"] == liquid - reserved - bills - debt


def test_the_prompt_forbids_deriving_spendable_money_by_subtraction() -> None:
    """The bug was in the prompt itself: it told the model to base affordability on
    'liquid assets MINUS emergency_fund_reserved', which is the wrong figure and is
    also the model doing its own arithmetic."""
    rules = ai_tools.GROUNDING_RULES

    assert "get_safe_to_spend" in rules
    assert "NEVER derive a spendable amount yourself" in rules
    assert "liquid assets MINUS emergency_fund_reserved" not in rules


def test_safe_to_spend_is_advertised_to_the_model() -> None:
    names = [tool.name for tool in ai_tools.build_tools()]

    assert "get_safe_to_spend" in names


def test_month_arg_parses_to_a_day_in_that_month() -> None:
    """The time-scoped tools accept an optional YYYY-MM to reach past months."""
    from datetime import date

    assert ai_tools._month_to_today({})[0] is None  # no arg -> current month
    today, err = ai_tools._month_to_today({"month": "2026-05"})
    assert err is None
    assert today == date(2026, 5, 15)
    _, bad = ai_tools._month_to_today({"month": "not-a-month"})
    assert bad is not None and "note" in bad


def test_time_scoped_tools_advertise_the_month_param() -> None:
    by_name = {t.name: t for t in ai_tools.build_tools()}
    for name in (
        "get_spending_by_category",
        "get_budgets",
        "get_spending_insights",
        "get_net_worth",
        "get_income_and_tax",
    ):
        assert "month" in by_name[name].parameters["properties"], name


def test_system_prompt_demands_concise_non_repeating_answers() -> None:
    """User feedback 2026-07-25: answers re-listed the same stats every turn.
    The prompt must tell the model to lead with the answer, not repeat covered
    figures, and offer depth instead of delivering it."""
    from datetime import date

    prompt = ai_tools.build_system_prompt(today=date(2026, 7, 25))
    assert "CONCISENESS" in prompt
    assert "re-list" in prompt
    assert "expand only when the user asks" in prompt


def test_system_prompt_grounds_todays_date() -> None:
    """The model must be told 'now' or it calls the current year the future."""
    from datetime import date

    prompt = ai_tools.build_system_prompt(today=date(2026, 7, 15))
    assert "2026-07-15" in prompt
    assert "July 2026" in prompt
    assert "already happened" in prompt.lower()


def test_household_context_carries_the_key_facts() -> None:
    ctx = ai_tools.build_household_context(
        currency="USD",
        first_name="Alex",
        member_count=2,
        earliest_month="2026-01",
        latest_month="2026-07",
    )
    assert "Alex" in ctx
    assert "2 members" in ctx
    assert "USD" in ctx
    assert "2026-01" in ctx and "2026-07" in ctx


def test_debt_outlook_exposes_each_debt_so_the_advisor_need_not_ask(
    demo_engine: Engine,
) -> None:
    from family_cfo_api import repository

    # A real modeled debt: balance, rate, and minimum all already stored.
    card = repository.create_account(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "Sapphire Card", "credit_card", "USD",
        annual_interest_rate=0.24, minimum_payment_minor=15_000,
    )
    repository.record_account_balance(demo_engine, card.id, -500_000)

    result = _execute(demo_engine, "get_debt_outlook", {})

    debts = {d["name"]: d for d in result["debts"]}
    assert "Sapphire Card" in debts
    card_out = debts["Sapphire Card"]
    assert card_out["balance"]["amount_minor"] == 500_000
    assert card_out["minimum_payment"]["amount_minor"] == 15_000
    assert card_out["annual_interest_rate"] == 0.24
    # $150 min vs ~$100 monthly interest on $5k @ 24% → it does amortize.
    assert card_out["interest_only"] is False
    # payoff_now clears it today: balance + ~one month's interest, never more.
    assert card_out["payoff_now"]["amount_minor"] == 500_000 + round(500_000 * 0.24 / 12)
    # A 24% card is high-rate: no low-priority steer.
    assert card_out["strategy_note"] is None


def test_grounding_rules_forbid_advising_overpayment_of_a_debt() -> None:
    prompt = ai_tools.build_system_prompt()
    assert "never tell the user to send more than a debt's balance" in prompt.lower()
    assert "payoff_now" in prompt
    # Debt-strategy guidance: rate-first, low-rate deprioritized, 401(k) nuance.
    assert "prioritize by interest rate" in prompt.lower()
    assert "401(k) loan" in prompt


def test_debt_outlook_flags_401k_loans_and_low_rate_debt(demo_engine: Engine) -> None:
    from family_cfo_api import repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    k401 = repository.create_account(
        demo_engine, hh, "401k Loan", "401k_loan", "USD",
        annual_interest_rate=0.095, minimum_payment_minor=52_400,
    )
    repository.record_account_balance(demo_engine, k401.id, -1_600_000)
    student = repository.create_account(
        demo_engine, hh, "Student Loan", "student_loan", "USD",
        annual_interest_rate=0.02125, minimum_payment_minor=7_800,
    )
    repository.record_account_balance(demo_engine, student.id, -380_000)

    debts = {d["name"]: d for d in _execute(demo_engine, "get_debt_outlook", {})["debts"]}
    # The 9.5% 401(k) loan is flagged as pay-to-yourself, not a true cost.
    assert "retirement account" in debts["401k Loan"]["strategy_note"]
    # The 2.375% student loan is flagged low priority, despite its small balance.
    assert "LOW priority" in debts["Student Loan"]["strategy_note"]


def test_debt_history_returns_monthly_totals_and_average(demo_engine: Engine) -> None:
    from datetime import date

    from family_cfo_api import finance_service, repository

    hh = fixtures.DEMO_HOUSEHOLD_ID
    loan = repository.create_account(demo_engine, hh, "Auto Loan", "auto_loan", "USD")
    repository.record_account_balance(demo_engine, loan.id, -1_000_000)  # owes $10,000 now
    # A loan payment three months back, so the series spans more than one month.
    repository.create_transaction(
        demo_engine, household_id=hh, account_id=loan.id, occurred_at=date(2026, 5, 10),
        amount_minor=50_000, currency="USD", merchant="Auto Loan Pmt", description=None,
        import_source=None, import_id=None, review_state="reviewed",
    )

    hist = finance_service.debt_history(demo_engine, hh, "USD", today=date(2026, 7, 15))

    assert hist.months_covered >= 2  # May, June, July
    assert [p.month for p in hist.points] == sorted(p.month for p in hist.points)  # chronological
    # Current owed = the demo mortgage (300,000,000) + this loan (1,000,000).
    assert hist.points[-1].total_owed.amount_minor == 301_000_000
    assert hist.average.amount_minor >= 1_000_000


def test_get_debt_history_tool_shape(demo_engine: Engine) -> None:
    from family_cfo_api import repository

    loan = repository.create_account(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, "L", "student_loan", "USD")
    repository.record_account_balance(demo_engine, loan.id, -500_000)

    result = _execute(demo_engine, "get_debt_history", {})

    assert "average_debt" in result and result["average_debt"]["currency"] == "USD"
    assert isinstance(result["months"], list)
    assert result["months_covered"] == len(result["months"])


def test_money_args_are_dollars_and_legacy_minor_still_works() -> None:
    # ADR 0063: the model speaks dollars; it once read its own cents input
    # back as dollars, 100x the intended figure.
    from family_cfo_api.ai_tools import _money_arg

    minor, error = _money_arg({"price": 49.99}, "price", minimum=0)
    assert error is None and minor == 4999

    minor, error = _money_arg({"price": 11000}, "price", minimum=0)
    assert error is None and minor == 1_100_000

    minor, error = _money_arg({"price_minor": 4999}, "price", minimum=0)
    assert error is None and minor == 4999

    missing, error = _money_arg({}, "price", minimum=0)
    assert missing is None and error["error"] == "missing_input"

    bad, error = _money_arg({"price": "lots"}, "price", minimum=0)
    assert bad is None and error["error"] == "invalid_arguments"


def test_tool_schemas_never_expose_minor_unit_inputs() -> None:
    # Guard: no model-facing input may be named *_minor again.
    from family_cfo_api.ai_tools import build_tools

    for tool in build_tools():
        for name in tool.parameters.get("properties", {}):
            assert not name.endswith("_minor"), f"{tool.name}.{name} exposes minor units"


def test_household_context_directs_the_answer_language() -> None:
    """#10: a non-English household gets every answer in its language, with
    tool-reported figures and names kept verbatim."""
    ctx = ai_tools.build_household_context(currency="USD", language="vi")
    assert "Vietnamese (vi)" in ctx
    ctx_lt = ai_tools.build_household_context(currency="USD", language="lt")
    assert "Lithuanian (lt)" in ctx_lt
    # English is the default voice — no directive line at all.
    assert "Answer in" not in ai_tools.build_household_context(currency="USD", language="en")
    assert "Answer in" not in ai_tools.build_household_context(currency="USD")
