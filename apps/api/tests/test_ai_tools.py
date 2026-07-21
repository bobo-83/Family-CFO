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
    assert result["missing"] == "present_value_minor"


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
    assert sum(a["balance_minor"] for a in funded) >= 28_500_000
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
        first_name="Bo",
        member_count=2,
        earliest_month="2026-01",
        latest_month="2026-07",
    )
    assert "Bo" in ctx
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
    # The 2.125% student loan is flagged low priority, despite its small balance.
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
