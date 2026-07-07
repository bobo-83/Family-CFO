from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)

from family_cfo_api.db import metadata

CURRENCY_LENGTH = 3

HOUSEHOLD_ROLES = ("owner", "adult", "viewer", "child")
ACCOUNT_TYPES = (
    "checking",
    "savings",
    "credit_card",
    "brokerage",
    "retirement",
    "hsa",
    "529",
    "mortgage",
    "auto_loan",
    "student_loan",
    "real_estate",
    "other_asset",
    "other_liability",
)
RECURRING_FREQUENCIES = ("weekly", "biweekly", "semimonthly", "monthly", "quarterly", "annual")
GOAL_TYPES = ("emergency_fund", "vacation", "retirement", "college", "vehicle", "renovation", "other")
CALCULATION_TYPES = ("net_worth", "cash_flow", "budget_summary", "emergency_fund", "goal_progress")
TRANSACTION_REVIEW_STATES = ("pending", "reviewed")


def _uuid_pk(name: str = "id") -> Column:
    return Column(name, String(36), primary_key=True)


def _currency_column(name: str = "currency") -> Column:
    return Column(name, String(CURRENCY_LENGTH), nullable=False)


households = Table(
    "households",
    metadata,
    _uuid_pk(),
    Column("display_name", String(120), nullable=False),
    _currency_column("base_currency"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint("length(base_currency) = 3", name="ck_households_currency_length"),
)

users = Table(
    "users",
    metadata,
    _uuid_pk(),
    Column("email", String(255), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False),
    Column("display_name", String(120), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

household_memberships = Table(
    "household_memberships",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("role", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(f"role in {HOUSEHOLD_ROLES!r}", name="ck_household_memberships_role"),
    UniqueConstraint("household_id", "user_id", name="uq_household_memberships_household_user"),
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    _uuid_pk(),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("token_hash", String(128), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

accounts = Table(
    "accounts",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("name", String(120), nullable=False),
    Column("type", String(30), nullable=False),
    _currency_column(),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(f"type in {ACCOUNT_TYPES!r}", name="ck_accounts_type"),
)

account_balances = Table(
    "account_balances",
    metadata,
    _uuid_pk(),
    Column("account_id", String(36), ForeignKey("accounts.id"), nullable=False),
    Column("balance_minor", BigInteger, nullable=False),
    Column("as_of", DateTime(timezone=True), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

transaction_categories = Table(
    "transaction_categories",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=True),
    Column("name", String(80), nullable=False),
    Column("parent_category_id", String(36), ForeignKey("transaction_categories.id"), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

transactions = Table(
    "transactions",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("account_id", String(36), ForeignKey("accounts.id"), nullable=False),
    Column("occurred_at", Date, nullable=False),
    Column("amount_minor", BigInteger, nullable=False),
    _currency_column(),
    Column("merchant", String(120), nullable=True),
    Column("category_id", String(36), ForeignKey("transaction_categories.id"), nullable=True),
    Column("description", Text, nullable=True),
    Column("import_source", String(30), nullable=True),
    Column("review_state", String(20), nullable=False, server_default="reviewed"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(f"review_state in {TRANSACTION_REVIEW_STATES!r}", name="ck_transactions_review_state"),
)

bills = Table(
    "bills",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("account_id", String(36), ForeignKey("accounts.id"), nullable=True),
    Column("name", String(120), nullable=False),
    Column("amount_minor", BigInteger, nullable=False),
    _currency_column(),
    Column("frequency", String(20), nullable=False),
    Column("next_due_date", Date, nullable=True),
    Column("category_id", String(36), ForeignKey("transaction_categories.id"), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(f"frequency in {RECURRING_FREQUENCIES!r}", name="ck_bills_frequency"),
)

income_sources = Table(
    "income_sources",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("name", String(120), nullable=False),
    Column("amount_minor", BigInteger, nullable=False),
    _currency_column(),
    Column("frequency", String(20), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(f"frequency in {RECURRING_FREQUENCIES!r}", name="ck_income_sources_frequency"),
)

goals = Table(
    "goals",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("name", String(120), nullable=False),
    Column("type", String(30), nullable=False),
    Column("target_minor", BigInteger, nullable=False),
    Column("current_minor", BigInteger, nullable=False, server_default="0"),
    _currency_column(),
    Column("target_date", Date, nullable=True),
    Column("priority", Integer, nullable=False, server_default="3"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(f"type in {GOAL_TYPES!r}", name="ck_goals_type"),
    CheckConstraint("priority between 1 and 5", name="ck_goals_priority"),
)

scenarios = Table(
    "scenarios",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("created_by_user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("name", String(120), nullable=False),
    Column("description", Text, nullable=True),
    Column("input_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

financial_calculations = Table(
    "financial_calculations",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("calculation_type", String(30), nullable=False),
    Column("version", String(20), nullable=False),
    Column("inputs_json", JSON, nullable=False),
    Column("assumptions_json", JSON, nullable=False),
    Column("warnings_json", JSON, nullable=False),
    Column("outputs_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(f"calculation_type in {CALCULATION_TYPES!r}", name="ck_financial_calculations_type"),
)
