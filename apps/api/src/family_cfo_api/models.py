from __future__ import annotations

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
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
GOAL_TYPES = (
    "emergency_fund",
    "vacation",
    "retirement",
    "college",
    "vehicle",
    "renovation",
    "other",
)
CALCULATION_TYPES = (
    "net_worth",
    "cash_flow",
    "budget_summary",
    "emergency_fund",
    "goal_progress",
    "purchase_impact",
    "debt_payoff",
    "retirement_projection",
    "future_value",
)
TRANSACTION_REVIEW_STATES = ("pending", "reviewed")
EXPLANATION_SOURCES = ("deterministic_stub", "llm", "agentic_tool_calling")
AI_RUNTIME_PROVIDERS = ("vllm", "ollama", "llama_cpp", "openai_compatible")
IMPORT_SOURCE_TYPES = ("csv", "pdf", "ofx", "qfx")
IMPORT_STATUSES = ("pending", "processing", "needs_review", "completed", "discarded", "failed")
DOCUMENT_EXTRACTION_TYPES = ("pdf_text", "ocr")
REPORT_TYPES = ("weekly", "monthly", "annual")
BACKUP_JOB_STATUSES = ("pending", "running", "completed", "failed")
CONVERSATION_MESSAGE_ROLES = ("user", "assistant")


def _uuid_pk(name: str = "id") -> Column:
    return Column(name, String(36), primary_key=True)


def _currency_column(name: str = "currency") -> Column:
    return Column(name, String(CURRENCY_LENGTH), nullable=False)


def _sql_in(values: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{value}'" for value in values) + ")"


households = Table(
    "households",
    metadata,
    _uuid_pk(),
    Column("display_name", String(120), nullable=False),
    _currency_column("base_currency"),
    # M43: null means "use the default target" (finance_service constant).
    Column("emergency_fund_target_months", Float, nullable=True),
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
    CheckConstraint(f"role in {_sql_in(HOUSEHOLD_ROLES)}", name="ck_household_memberships_role"),
    UniqueConstraint("household_id", "user_id", name="uq_household_memberships_household_user"),
)

pairing_sessions = Table(
    "pairing_sessions",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("created_by_user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("qr_payload", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("confirmed_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

paired_devices = Table(
    "paired_devices",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("name", String(120), nullable=False),
    Column("public_key", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), nullable=True),
    Column("revoked_at", DateTime(timezone=True), nullable=True),
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    _uuid_pk(),
    Column("user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("device_id", String(36), ForeignKey("paired_devices.id"), nullable=True),
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
    # Debt terms, meaningful only for liability account types (M14). Nullable —
    # an account without both set is not modeled for payoff.
    Column("annual_interest_rate", Float, nullable=True),
    Column("minimum_payment_minor", BigInteger, nullable=True),
    # Emergency fund designation (M36): percent of balance OR a fixed amount,
    # never both. Reserved money is derived at read time from the latest balance.
    Column("emergency_fund_percent", Float, nullable=True),
    Column("emergency_fund_minor", BigInteger, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(f"type in {_sql_in(ACCOUNT_TYPES)}", name="ck_accounts_type"),
    CheckConstraint(
        "emergency_fund_percent IS NULL OR emergency_fund_minor IS NULL",
        name="ck_accounts_emergency_fund_exclusive",
    ),
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

# M40: one net-worth snapshot per household per day, for the Overview trend.
net_worth_snapshots = Table(
    "net_worth_snapshots",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("as_of", Date, nullable=False),
    Column("net_worth_minor", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("household_id", "as_of", name="uq_net_worth_snapshots_household_day"),
)

transaction_categories = Table(
    "transaction_categories",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=True),
    Column("name", String(80), nullable=False),
    Column(
        "parent_category_id", String(36), ForeignKey("transaction_categories.id"), nullable=True
    ),
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
    Column(
        "import_id",
        String(36),
        ForeignKey("imports.id", name="fk_transactions_import_id"),
        nullable=True,
    ),
    Column("possible_duplicate", Boolean, nullable=False, server_default="0"),
    Column("review_state", String(20), nullable=False, server_default="reviewed"),
    # M27 dedupe (ADR 0015): provider transaction id (hard idempotency) and a
    # content hash (soft fallback for CSV rows without provider ids).
    Column("external_id", String(120), nullable=True),
    Column("import_hash", String(64), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        f"review_state in {_sql_in(TRANSACTION_REVIEW_STATES)}", name="ck_transactions_review_state"
    ),
    Index(
        "uq_transactions_account_external",
        "account_id",
        "external_id",
        unique=True,
        sqlite_where=text("external_id IS NOT NULL"),
        postgresql_where=text("external_id IS NOT NULL"),
    ),
    Index("ix_transactions_import_hash", "account_id", "import_hash"),
)

# --- M27: institution connections (ADR 0015) ---------------------------------

institution_connections = Table(
    "institution_connections",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("provider", String(30), nullable=False),  # "simplefin" for now
    Column("display_name", String(120), nullable=False),
    # Fernet-encrypted SimpleFIN access URL — a credential; never returned by the API.
    Column("access_url_encrypted", Text, nullable=False),
    Column("status", String(20), nullable=False, server_default="active"),
    Column("last_synced_at", DateTime(timezone=True), nullable=True),
    Column("last_sync_error", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

connection_accounts = Table(
    "connection_accounts",
    metadata,
    _uuid_pk(),
    Column(
        "connection_id",
        String(36),
        ForeignKey("institution_connections.id", name="fk_connection_accounts_connection_id"),
        nullable=False,
    ),
    Column("external_account_id", String(120), nullable=False),
    Column("account_id", String(36), ForeignKey("accounts.id"), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Index(
        "uq_connection_accounts_external",
        "connection_id",
        "external_account_id",
        unique=True,
    ),
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
    CheckConstraint(f"frequency in {_sql_in(RECURRING_FREQUENCIES)}", name="ck_bills_frequency"),
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
    CheckConstraint(
        f"frequency in {_sql_in(RECURRING_FREQUENCIES)}", name="ck_income_sources_frequency"
    ),
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
    CheckConstraint(f"type in {_sql_in(GOAL_TYPES)}", name="ck_goals_type"),
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
    CheckConstraint(
        f"calculation_type in {_sql_in(CALCULATION_TYPES)}", name="ck_financial_calculations_type"
    ),
)

recommendations = Table(
    "recommendations",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("scenario_id", String(36), ForeignKey("scenarios.id"), nullable=True),
    Column("answer", Text, nullable=False),
    Column("assumptions_json", JSON, nullable=False),
    Column("impacts_json", JSON, nullable=False),
    Column("tradeoffs_json", JSON, nullable=False),
    Column("alternatives_json", JSON, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("calculation_refs_json", JSON, nullable=False),
    Column("warnings_json", JSON, nullable=False),
    Column("explanation_source", String(30), nullable=False),
    Column("model_version", String(100), nullable=True),
    Column("prompt_version", String(50), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        f"explanation_source in {_sql_in(EXPLANATION_SOURCES)}",
        name="ck_recommendations_explanation_source",
    ),
    CheckConstraint(
        "confidence >= 0 and confidence <= 1", name="ck_recommendations_confidence_range"
    ),
)

ai_runtime_configs = Table(
    "ai_runtime_configs",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False, unique=True),
    Column("provider", String(30), nullable=False),
    Column("base_url", String(255), nullable=False),
    Column("model", String(100), nullable=False),
    Column("enabled", Boolean, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        f"provider in {_sql_in(AI_RUNTIME_PROVIDERS)}", name="ck_ai_runtime_configs_provider"
    ),
)

imports = Table(
    "imports",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("account_id", String(36), ForeignKey("accounts.id"), nullable=True),
    Column("source_type", String(20), nullable=False),
    Column("filename", String(255), nullable=False),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("error_message", Text, nullable=True),
    Column("skipped_row_count", Integer, nullable=False, server_default="0"),
    Column("retry_count", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        f"source_type in {_sql_in(IMPORT_SOURCE_TYPES)}", name="ck_imports_source_type"
    ),
    CheckConstraint(f"status in {_sql_in(IMPORT_STATUSES)}", name="ck_imports_status"),
)

import_files = Table(
    "import_files",
    metadata,
    _uuid_pk(),
    Column("import_id", String(36), ForeignKey("imports.id"), nullable=False, unique=True),
    Column("storage_path", String(500), nullable=False),
    Column("content_type", String(100), nullable=False),
    Column("size_bytes", BigInteger, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

documents = Table(
    "documents",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("import_id", String(36), ForeignKey("imports.id"), nullable=True),
    Column("content_type", String(100), nullable=False),
    Column("storage_path", String(500), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

document_extractions = Table(
    "document_extractions",
    metadata,
    _uuid_pk(),
    Column("document_id", String(36), ForeignKey("documents.id"), nullable=False, unique=True),
    Column("extraction_type", String(20), nullable=False),
    Column("text", Text, nullable=False),
    Column("structured_fields_json", JSON, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("warnings_json", JSON, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        f"extraction_type in {_sql_in(DOCUMENT_EXTRACTION_TYPES)}",
        name="ck_document_extractions_type",
    ),
    CheckConstraint(
        "confidence >= 0 and confidence <= 1", name="ck_document_extractions_confidence_range"
    ),
)

reports = Table(
    "reports",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("report_type", String(20), nullable=False),
    Column("period_start", Date, nullable=False),
    Column("period_end", Date, nullable=False),
    Column("summary_json", JSON, nullable=False),
    Column("explanation_text", Text, nullable=False),
    Column("explanation_source", String(30), nullable=False),
    Column("model_version", String(100), nullable=True),
    Column("prompt_version", String(50), nullable=True),
    Column("calculation_version", String(20), nullable=False),
    Column("generated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint(
        "household_id", "report_type", "period_start", name="uq_reports_household_type_period"
    ),
    CheckConstraint(f"report_type in {_sql_in(REPORT_TYPES)}", name="ck_reports_type"),
    CheckConstraint(
        f"explanation_source in {_sql_in(EXPLANATION_SOURCES)}",
        name="ck_reports_explanation_source",
    ),
)

backup_jobs = Table(
    "backup_jobs",
    metadata,
    _uuid_pk(),
    Column("status", String(20), nullable=False, server_default="pending"),
    Column("storage_path", String(500), nullable=True),
    Column("size_bytes", BigInteger, nullable=True),
    Column("error_message", Text, nullable=True),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("completed_at", DateTime(timezone=True), nullable=True),
    Column("pruned_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(f"status in {_sql_in(BACKUP_JOB_STATUSES)}", name="ck_backup_jobs_status"),
)

audit_events = Table(
    "audit_events",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("actor_user_id", String(36), ForeignKey("users.id"), nullable=True),
    Column("action", String(60), nullable=False),
    Column("entity_type", String(40), nullable=False),
    Column("entity_id", String(36), nullable=True),
    Column("summary", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)

conversations = Table(
    "conversations",
    metadata,
    _uuid_pk(),
    Column("household_id", String(36), ForeignKey("households.id"), nullable=False),
    Column("created_by_user_id", String(36), ForeignKey("users.id"), nullable=False),
    Column("title", String(200), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

conversation_messages = Table(
    "conversation_messages",
    metadata,
    _uuid_pk(),
    Column("conversation_id", String(36), ForeignKey("conversations.id"), nullable=False),
    Column("role", String(20), nullable=False),
    Column("content", Text, nullable=False),
    Column("recommendation_id", String(36), ForeignKey("recommendations.id"), nullable=True),
    Column("sequence", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        f"role in {_sql_in(CONVERSATION_MESSAGE_ROLES)}", name="ck_conversation_messages_role"
    ),
)
