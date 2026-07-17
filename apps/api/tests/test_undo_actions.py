"""M108/M110: undo framework — reversing create/update/delete on household records."""

import json
from datetime import date

import pytest
from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, repository, undo_actions

HH = fixtures.DEMO_HOUSEHOLD_ID


def _reverse(engine: Engine, token: str) -> None:
    undo_actions.reverse(engine, HH, json.loads(token))


def _make_transaction(engine: Engine, note: str | None = None) -> str:
    account = repository.create_account(
        engine, HH, name="Undo Checking", account_type="checking", currency="USD"
    )
    tid = repository.create_transaction(
        engine, HH, account_id=account.id, occurred_at=date(2026, 7, 1),
        amount_minor=-1234, currency="USD", merchant="Corner Store",
        description=None, import_source=None, import_id=None, review_state="reviewed",
    )
    if note is not None:
        repository.set_transaction_note(engine, HH, tid, note)
    return tid


def test_bill_delete_is_undone_by_recreating(demo_engine: Engine) -> None:
    bill = repository.create_bill(
        demo_engine, HH, name="Undo Bill", amount_minor=1234, currency="USD",
        frequency="monthly",
    )
    token = undo_actions.bill_deleted(bill)
    repository.delete_bill(demo_engine, HH, bill.id)
    assert repository.get_bill(demo_engine, HH, bill.id) is None

    _reverse(demo_engine, token)

    recreated = [b for b in repository.list_bills(demo_engine, HH) if b.name == "Undo Bill"]
    assert len(recreated) == 1
    assert recreated[0].amount_minor == 1234


def test_bill_create_is_undone_by_deleting(demo_engine: Engine) -> None:
    bill = repository.create_bill(
        demo_engine, HH, name="Ephemeral", amount_minor=500, currency="USD",
        frequency="monthly",
    )
    _reverse(demo_engine, undo_actions.created("bill", bill.id))
    assert repository.get_bill(demo_engine, HH, bill.id) is None


def test_bill_update_is_undone_by_restoring_previous(demo_engine: Engine) -> None:
    bill = repository.create_bill(
        demo_engine, HH, name="Before", amount_minor=1000, currency="USD",
        frequency="monthly",
    )
    token = undo_actions.bill_updated(bill)
    repository.update_bill(demo_engine, HH, bill.id, name="After", amount_minor=9999)

    _reverse(demo_engine, token)

    after = repository.get_bill(demo_engine, HH, bill.id)
    assert after is not None
    assert after.name == "Before"
    assert after.amount_minor == 1000


def test_category_delete_is_undone_by_recreating(demo_engine: Engine) -> None:
    category = repository.create_category(demo_engine, HH, "UndoCat")
    token = undo_actions.category_deleted(category)
    repository.delete_category(demo_engine, HH, category.id)

    _reverse(demo_engine, token)

    assert any(c.name == "UndoCat" for c in repository.list_categories(demo_engine, HH))


def test_budget_update_is_undone_by_restoring_limit(demo_engine: Engine) -> None:
    category = repository.create_category(demo_engine, HH, "BudgetCat")
    budget_id = repository.create_budget(demo_engine, HH, category.id, 5000, "USD")
    before = repository.get_budget(demo_engine, HH, budget_id)
    assert before is not None
    token = undo_actions.budget_updated(before)
    repository.update_budget_limit(demo_engine, HH, budget_id, 99999)

    _reverse(demo_engine, token)

    after = repository.get_budget(demo_engine, HH, budget_id)
    assert after is not None
    assert after.limit_minor == 5000


def test_transaction_note_edit_is_undone_by_restoring(demo_engine: Engine) -> None:
    tid = _make_transaction(demo_engine, note="Verizon FIOS")
    before = repository.get_transaction(demo_engine, HH, tid)
    assert before is not None
    token = undo_actions.transaction_updated(before)
    repository.set_transaction_note(demo_engine, HH, tid, "something else")

    _reverse(demo_engine, token)

    after = repository.get_transaction(demo_engine, HH, tid)
    assert after is not None
    assert after.note == "Verizon FIOS"


def test_transaction_delete_is_undone_by_recreating(demo_engine: Engine) -> None:
    tid = _make_transaction(demo_engine, note="keep me")
    before = repository.get_transaction(demo_engine, HH, tid)
    assert before is not None
    token = undo_actions.transaction_deleted(before)
    repository.delete_transaction(demo_engine, HH, tid)
    assert repository.get_transaction(demo_engine, HH, tid) is None

    _reverse(demo_engine, token)

    restored = repository.get_transaction(demo_engine, HH, tid)
    assert restored is not None
    assert restored.id == tid  # same id, so references survive
    assert restored.note == "keep me"
    assert restored.amount_minor == -1234


def test_income_delete_is_undone_by_recreating(demo_engine: Engine) -> None:
    record = repository.create_income_source(
        demo_engine, HH, name="Side Gig", amount_minor=250000, currency="USD",
        frequency="monthly",
    )
    token = undo_actions.income_deleted(record)
    repository.delete_income_source(demo_engine, HH, record.id)

    _reverse(demo_engine, token)

    restored = [i for i in repository.list_income_sources(demo_engine, HH) if i.name == "Side Gig"]
    assert len(restored) == 1
    assert restored[0].amount_minor == 250000


def test_memory_delete_is_undone_by_recreating(demo_engine: Engine) -> None:
    record = repository.upsert_household_memory(
        demo_engine, HH, "note_undo", "buys oat milk", source="manual"
    )
    token = undo_actions.memory_deleted(record)
    repository.delete_household_memory(demo_engine, HH, record.id)

    _reverse(demo_engine, token)

    assert any(
        m.value == "buys oat milk" for m in repository.list_household_memories(demo_engine, HH)
    )


def test_suggestion_dismissal_is_undone_by_removing_the_dismissal(demo_engine: Engine) -> None:
    repository.add_bill_suggestion_dismissal(demo_engine, HH, "netflix com")
    assert "netflix com" in repository.list_bill_suggestion_dismissals(demo_engine, HH)

    _reverse(demo_engine, undo_actions.suggestion_dismissed("netflix com"))

    assert "netflix com" not in repository.list_bill_suggestion_dismissals(demo_engine, HH)


def test_recorded_balance_is_undone_by_deleting_the_snapshot(demo_engine: Engine) -> None:
    account = repository.create_account(
        demo_engine, HH, name="Undo Balance", account_type="checking", currency="USD"
    )
    repository.record_account_balance(demo_engine, account.id, 100_000)
    newer = repository.record_account_balance(demo_engine, account.id, 250_000)

    _reverse(demo_engine, undo_actions.balance_recorded(newer))

    balances = {b.account_id: b for b in repository.list_account_balances(demo_engine, HH)}
    assert balances[account.id].balance_minor == 100_000  # prior snapshot is current again


def test_income_override_is_undone_by_restoring_the_previous_verdict(
    demo_engine: Engine,
) -> None:
    tid = _make_transaction(demo_engine)
    repository.set_income_override(demo_engine, HH, tid, "exclude")
    token = undo_actions.income_override_set(tid, "exclude")
    repository.set_income_override(demo_engine, HH, tid, "include")

    _reverse(demo_engine, token)
    assert repository.list_income_overrides(demo_engine, HH)[tid] == "exclude"

    # And undoing the FIRST override clears it entirely.
    _reverse(demo_engine, undo_actions.income_override_set(tid, None))
    assert tid not in repository.list_income_overrides(demo_engine, HH)


def test_member_removal_is_undone_by_restoring_the_membership(demo_engine: Engine) -> None:
    member = repository.create_member(
        demo_engine, HH, email="undo@example.com", password_hash="x" * 20,
        display_name="Undo Member", role="adult",
    )
    token = undo_actions.member_removed(member.user_id, "adult")
    repository.delete_member(demo_engine, HH, member.user_id)
    assert repository.get_member(demo_engine, HH, member.user_id) is None

    _reverse(demo_engine, token)

    restored = repository.get_member(demo_engine, HH, member.user_id)
    assert restored is not None and restored.role == "adult"


def test_role_change_is_undone_by_restoring_the_previous_role(demo_engine: Engine) -> None:
    member = repository.create_member(
        demo_engine, HH, email="role@example.com", password_hash="x" * 20,
        display_name="Role Member", role="viewer",
    )
    token = undo_actions.member_role_changed(member.user_id, "viewer")
    repository.update_member_role(demo_engine, HH, member.user_id, "adult")

    _reverse(demo_engine, token)

    restored = repository.get_member(demo_engine, HH, member.user_id)
    assert restored is not None and restored.role == "viewer"


def test_tax_settings_are_undone_by_restoring_before_values(demo_engine: Engine) -> None:
    household = repository.get_household(demo_engine, HH)
    assert household is not None
    token = undo_actions.tax_settings_updated(household)
    repository.update_tax_settings(
        demo_engine, HH, tax_filing_status="single", income_treated_as_net=False, state="NJ"
    )

    _reverse(demo_engine, token)

    after = repository.get_household(demo_engine, HH)
    assert after is not None
    assert after.tax_filing_status == household.tax_filing_status
    assert after.state == household.state


def test_income_profile_delete_is_undone_by_recreating(demo_engine: Engine) -> None:
    profile_id = repository.create_income_profile(
        demo_engine, HH, label="Undo Earner", base_salary_minor=10_000_000
    )
    before = next(
        r for r in repository.list_income_profiles(demo_engine, HH) if r.id == profile_id
    )
    token = undo_actions.income_profile_deleted(before)
    repository.delete_income_profile(demo_engine, HH, profile_id)

    _reverse(demo_engine, token)

    restored = [r for r in repository.list_income_profiles(demo_engine, HH) if r.label == "Undo Earner"]
    assert len(restored) == 1 and restored[0].base_salary_minor == 10_000_000


def test_attachment_add_is_undone_by_restoring_prior_fields(demo_engine: Engine) -> None:
    tid = _make_transaction(demo_engine)
    before = repository.get_transaction(demo_engine, HH, tid)
    assert before is not None and before.attachment_path is None
    token = undo_actions.transaction_updated(before)
    repository.set_transaction_attachment(demo_engine, HH, tid, "attachments/x.jpg", "image/jpeg")

    _reverse(demo_engine, token)

    after = repository.get_transaction(demo_engine, HH, tid)
    assert after is not None and after.attachment_path is None


def test_goal_update_is_undone_by_restoring_previous(demo_engine: Engine) -> None:
    goal = repository.create_goal(
        demo_engine, HH, name="Undo Goal", goal_type="vacation",
        target_minor=500_000, currency="USD", target_date=None, priority=3,
        monthly_contribution_minor=25_000,
    )
    token = undo_actions.goal_updated(goal)
    repository.update_goal(
        demo_engine, HH, goal.id, name="Renamed", monthly_contribution_minor=99_900
    )

    _reverse(demo_engine, token)

    after = repository.get_goal(demo_engine, HH, goal.id)
    assert after is not None
    assert after.name == "Undo Goal"
    assert after.monthly_contribution_minor == 25_000


def test_goal_delete_is_undone_by_recreating(demo_engine: Engine) -> None:
    goal = repository.create_goal(
        demo_engine, HH, name="Undo Goal Del", goal_type="vacation",
        target_minor=500_000, currency="USD", target_date=None, priority=2,
        monthly_contribution_minor=30_000,
    )
    token = undo_actions.goal_deleted(goal)
    repository.delete_goal(demo_engine, HH, goal.id)
    assert repository.get_goal(demo_engine, HH, goal.id) is None

    _reverse(demo_engine, token)

    restored = [
        g for g in repository.list_goals(demo_engine, HH) if g.name == "Undo Goal Del"
    ]
    assert len(restored) == 1
    assert restored[0].monthly_contribution_minor == 30_000
    assert restored[0].priority == 2


def test_inherently_irreversible_action_raises(demo_engine: Engine) -> None:
    # A login / backup-ran / key-reveal records no undo token; a stray unknown shape
    # must be refused, not silently no-op.
    with pytest.raises(undo_actions.UndoError):
        undo_actions.reverse(demo_engine, HH, {"op": "backup_ran"})
