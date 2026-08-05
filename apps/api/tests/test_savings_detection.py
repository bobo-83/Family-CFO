"""#201: recurring savings-contribution detection — the rules that make it
honest, not just the happy path."""

from datetime import date, timedelta

from family_cfo_api.savings_detection import (
    LedgerEntry,
    SavingsAccount,
    detect_contributions,
    monthly_equivalent_minor,
)

CHECKING = SavingsAccount("acct-checking", "Everyday Checking", "checking")
FIVE29 = SavingsAccount("acct-529", "College 529", "529")
BROKERAGE = SavingsAccount("acct-brk", "Taxable brokerage", "brokerage")
ACCOUNTS = [CHECKING, FIVE29, BROKERAGE]

TODAY = date(2026, 8, 5)


def _transfer(n: int, when: date, amount: int, *, dest="acct-529", src="acct-checking"):
    """Both legs of one internal transfer."""
    return [
        LedgerEntry(f"out-{n}", src, when, -amount, "USD"),
        LedgerEntry(f"in-{n}", dest, when, amount, "USD"),
    ]


def _monthly_series(count: int, amount: int, **kwargs) -> list[LedgerEntry]:
    entries: list[LedgerEntry] = []
    for i in range(count):
        # ~30-day spacing walking backwards from TODAY.
        entries += _transfer(i, TODAY - timedelta(days=30 * i), amount, **kwargs)
    return entries


def test_detects_the_monthly_529_contribution() -> None:
    found = detect_contributions(_monthly_series(4, 50_000), ACCOUNTS, today=TODAY)

    assert len(found) == 1
    contribution = found[0]
    assert contribution.destination_name == "College 529"
    assert contribution.destination_type == "529"
    assert contribution.amount_minor == 50_000
    assert contribution.frequency == "monthly"
    assert contribution.occurrences == 4
    assert contribution.source_account_id == "acct-checking"
    # The next one is expected about a month after the last sighting.
    assert contribution.next_expected > contribution.last_seen


def test_amount_drift_within_tolerance_still_counts() -> None:
    """A bump from $500 to $520 is the same habit, not a new one."""
    entries = (
        _transfer(0, TODAY, 52_000)
        + _transfer(1, TODAY - timedelta(days=30), 50_000)
        + _transfer(2, TODAY - timedelta(days=60), 50_000)
        + _transfer(3, TODAY - timedelta(days=90), 50_000)
    )
    found = detect_contributions(entries, ACCOUNTS, today=TODAY)
    assert len(found) == 1
    assert found[0].occurrences == 4


def test_one_off_transfer_is_not_a_contribution() -> None:
    """Moving a bonus into savings once is not a savings habit."""
    found = detect_contributions(_transfer(0, TODAY, 500_000), ACCOUNTS, today=TODAY)
    assert found == []


def test_churn_out_and_back_is_not_saving() -> None:
    """Money shuttled to the brokerage and pulled back is not saving (#201)."""
    entries = _monthly_series(4, 40_000, dest="acct-brk")
    # Every contribution comes straight back a day later.
    for i in range(4):
        when = TODAY - timedelta(days=30 * i) + timedelta(days=1)
        entries += [
            LedgerEntry(f"rev-out-{i}", "acct-brk", when, -40_000, "USD"),
            LedgerEntry(f"rev-in-{i}", "acct-checking", when, 40_000, "USD"),
        ]

    assert detect_contributions(entries, ACCOUNTS, today=TODAY) == []


def test_transfer_to_a_non_savings_account_is_ignored() -> None:
    """Checking-to-checking movement is not saving, however regular."""
    other_checking = SavingsAccount("acct-checking-2", "Joint Checking", "checking")
    entries = _monthly_series(4, 30_000, dest="acct-checking-2")
    found = detect_contributions(entries, [CHECKING, other_checking], today=TODAY)
    assert found == []


def test_tiny_transfers_are_noise() -> None:
    found = detect_contributions(_monthly_series(4, 500), ACCOUNTS, today=TODAY)
    assert found == []


def test_two_destinations_are_reported_largest_first() -> None:
    entries = _monthly_series(4, 50_000) + _monthly_series(4, 20_000, dest="acct-brk")
    # Distinct ids so the two series don't collide.
    entries = [
        LedgerEntry(f"{e.transaction_id}-{e.account_id}", e.account_id, e.occurred_at,
                    e.amount_minor, e.currency)
        for e in entries
    ]
    found = detect_contributions(entries, ACCOUNTS, today=TODAY)
    assert [c.destination_type for c in found] == ["529", "brokerage"]
    assert [c.amount_minor for c in found] == [50_000, 20_000]


def test_monthly_equivalent_normalizes_cadences() -> None:
    entries = _monthly_series(4, 50_000)
    monthly = detect_contributions(entries, ACCOUNTS, today=TODAY)[0]
    assert monthly_equivalent_minor(monthly) == 50_000


def test_advisor_tool_reports_contributions_with_a_coverage_note(demo_engine) -> None:
    """#201: the tool must never let a transfer-derived figure read as the
    household's total saving — payroll deductions are invisible here."""
    from family_cfo_api import ai_tools, repository

    hh = repository.list_households(demo_engine)[0]
    checking = repository.create_account(demo_engine, hh, "Everyday", "checking", "USD")
    college = repository.create_account(demo_engine, hh, "College 529", "529", "USD")
    for i in range(4):
        when = date.today() - timedelta(days=30 * i)
        repository.create_transaction(
            demo_engine, hh, checking.id, when, -50_000, "USD",
            "Transfer to 529", None, None, None, "reviewed",
        )
        repository.create_transaction(
            demo_engine, hh, college.id, when, 50_000, "USD",
            "Contribution", None, None, None, "reviewed",
        )

    payload = ai_tools._get_savings_contributions(demo_engine, hh, "USD", {})
    contributions = payload["contributions"]
    assert any(c["destination"] == "College 529" for c in contributions)
    detected = next(c for c in contributions if c["destination"] == "College 529")
    assert detected["frequency"] == "monthly"
    assert detected["monthly_equivalent"]["amount_minor"] == 50_000
    assert "payroll" in payload["coverage_note"].lower()
