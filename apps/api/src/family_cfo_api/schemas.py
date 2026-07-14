from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

HouseholdRole = Literal["owner", "adult", "viewer", "child"]
AccountType = Literal[
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
]
GoalType = Literal[
    "emergency_fund", "vacation", "retirement", "college", "vehicle", "renovation", "other"
]
RecurringFrequency = Literal["weekly", "biweekly", "semimonthly", "monthly", "quarterly", "annual"]
PurchaseSource = Literal["manual", "mobile_vision", "receipt", "product_photo"]
ImpactArea = Literal[
    "cash_flow", "emergency_fund", "debt", "savings_goal", "retirement", "net_worth", "other"
]
AiRuntimeProvider = Literal["vllm", "ollama", "llama_cpp", "openai_compatible"]
ImportSourceType = Literal["csv", "pdf", "ofx", "qfx"]
ImportStatus = Literal["pending", "processing", "needs_review", "completed", "discarded", "failed"]
DocumentExtractionType = Literal["pdf_text", "ocr"]
ReportType = Literal["weekly", "monthly", "annual"]
ExplanationSource = Literal["deterministic_stub", "llm"]
BackupJobStatus = Literal["pending", "running", "completed", "failed"]


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    version: str


class ApiError(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ApiError


class Money(BaseModel):
    amount_minor: int
    currency: str = Field(min_length=3, max_length=3)


class AuthSessionCreateRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)


class AuthSession(BaseModel):
    access_token: str
    expires_at: datetime
    household_id: str
    user_id: str
    role: HouseholdRole


class PairingSession(BaseModel):
    id: str
    qr_payload: str
    expires_at: datetime


class PairingConfirmRequest(BaseModel):
    pairing_session_id: str
    device_name: str = Field(min_length=1, max_length=120)
    device_public_key: str = Field(min_length=1)


class DeviceCredential(BaseModel):
    device_id: str
    access_token: str
    expires_at: datetime
    # M83: role of the user the device acts as (the pairing session's
    # creator), so the mobile app can build its role-aware shell without
    # spending the device token on a session refresh.
    role: HouseholdRole | None = None


class PairedDevice(BaseModel):
    id: str
    name: str
    created_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None


class PairedDeviceListResponse(BaseModel):
    devices: list[PairedDevice]


class EmergencyFundSummary(BaseModel):
    """M38: coverage vs the standard 3–6 month guidance."""

    months: float | None = None
    reserved: Money
    using_designations: bool
    monthly_expenses: Money
    target_months_min: float
    target_months_recommended: float
    gap_to_recommended: Money | None = None
    # M75: the household's own emergency_fund goal target, when one exists —
    # the status is the more conservative of months-coverage and goal progress.
    goal_target: Money | None = None
    status: Literal["no_bills", "no_fund", "getting_started", "on_track", "fully_funded"]


class MonthlyCashFlow(BaseModel):
    income: Money
    bills: Money
    net: Money


class AssetCategoryTotal(BaseModel):
    category: Literal["liquid", "investments", "retirement", "education", "property"]
    total: Money


class UpcomingBill(BaseModel):
    """M39: a bill due within the overview look-ahead window."""

    id: str
    name: str
    amount: Money
    due_date: date
    days_until: int


class NetWorthPoint(BaseModel):
    """M40: one net-worth snapshot in the Overview trend series."""

    as_of: date
    net_worth: Money


class GoalProgress(BaseModel):
    """M41: the highest-priority goal, with progress toward its target."""

    id: str
    name: str
    type: GoalType
    current: Money
    target: Money
    percent_complete: int
    target_date: date | None = None


class MerchantSpend(BaseModel):
    merchant: str
    amount: Money


class SavingsRate(BaseModel):
    """M44: recurring income vs trailing-3-month average actual spending."""

    percent: int | None = None
    monthly_income: Money
    average_monthly_spending: Money


BudgetStatus = Literal["under", "warning", "over"]


class Budget(BaseModel):
    """M46: a monthly per-category envelope with current-month progress."""

    id: str
    category_id: str
    category_name: str
    limit: Money
    spent: Money
    remaining: Money
    percent_used: int
    status: BudgetStatus


class BudgetListResponse(BaseModel):
    budgets: list[Budget]


class BudgetCreateRequest(BaseModel):
    category_id: str
    limit: Money


class BudgetUpdateRequest(BaseModel):
    limit: Money


class BudgetSummary(BaseModel):
    """M46: envelope health for the Overview alert card."""

    envelope_count: int
    over_count: int
    warning_count: int
    total_budgeted: Money
    total_spent: Money


class SpendingInsights(BaseModel):
    """M42: month-to-date spending vs the same period last month, plus top merchants."""

    this_month: Money
    last_month: Money
    change_percent: int | None = None
    top_merchants: list[MerchantSpend] = Field(default_factory=list)


class CategorySpend(BaseModel):
    """M94: one category's spend this month."""

    category_id: str
    category_name: str
    amount: Money


class SpendingByCategory(BaseModel):
    """M94: this month's outflow grouped by category — the visible payoff of
    categorizing. `uncategorized` is what's still unsorted, so the user can see
    the value of filing more."""

    month: str
    month_label: str
    categories: list[CategorySpend] = Field(default_factory=list)
    categorized_total: Money
    uncategorized: Money


class SafeToSpend(BaseModel):
    """M93: what's actually free to spend now — liquid cash net of the emergency
    fund, bills due, and minimum debt payments. total_debt is reported (not
    subtracted) so spendable cash is never shown without the debt beside it."""

    liquid_balance: Money
    emergency_fund_reserved: Money
    bills_due: Money
    minimum_debt_payments: Money
    committed_total: Money
    safe_to_spend: Money
    total_debt: Money
    warnings: list[str] = Field(default_factory=list)


class HouseholdContext(BaseModel):
    household_id: str
    display_name: str
    currency: str
    net_worth: Money
    emergency_fund_months: float | None
    # M38: enriched overview summary (additive).
    emergency_fund: EmergencyFundSummary | None = None
    monthly_cash_flow: MonthlyCashFlow | None = None
    asset_breakdown: list[AssetCategoryTotal] = Field(default_factory=list)
    total_debt: Money | None = None
    upcoming_bills: list[UpcomingBill] = Field(default_factory=list)
    net_worth_history: list[NetWorthPoint] = Field(default_factory=list)
    top_goal: GoalProgress | None = None
    spending_insights: SpendingInsights | None = None
    savings_rate: SavingsRate | None = None
    budget_summary: BudgetSummary | None = None
    safe_to_spend: SafeToSpend | None = None
    spending_by_category: SpendingByCategory | None = None


class Account(BaseModel):
    id: str
    name: str
    type: AccountType
    balance: Money
    annual_interest_rate: float | None = None
    minimum_payment: Money | None = None
    # M33: set when the account is fed by a linked institution (M27).
    institution: str | None = None
    last_synced_at: datetime | None = None
    # M36: emergency-fund designation (percent XOR fixed amount) + derived reservation.
    emergency_fund_percent: float | None = None
    emergency_fund_amount: Money | None = None
    emergency_fund_reserved: Money | None = None


class Transaction(BaseModel):
    id: str
    account_id: str
    occurred_at: date
    amount: Money
    merchant: str | None = None
    category: str | None = None
    category_id: str | None = None
    description: str | None = None


class Category(BaseModel):
    id: str
    name: str


class CategoryListResponse(BaseModel):
    categories: list[Category]


class CategoryCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class Bill(BaseModel):
    id: str
    name: str
    amount: Money
    frequency: RecurringFrequency
    next_due_date: date | None = None
    account_id: str | None = None
    # M96: the spending category this bill is filed under (e.g. Subscriptions).
    category_id: str | None = None
    category_name: str | None = None


class IncomeSource(BaseModel):
    id: str
    name: str
    amount: Money
    frequency: RecurringFrequency


class Goal(BaseModel):
    id: str
    name: str
    type: GoalType
    target: Money
    current: Money
    target_date: date | None = None
    priority: int = Field(ge=1, le=5)


class GoalCreateRequest(BaseModel):
    name: str
    type: GoalType
    target: Money
    target_date: date | None = None
    priority: int = Field(default=3, ge=1, le=5)


class AccountListResponse(BaseModel):
    accounts: list[Account]


class GoalListResponse(BaseModel):
    goals: list[Goal]


class TransactionListResponse(BaseModel):
    transactions: list[Transaction]


class BillListResponse(BaseModel):
    bills: list[Bill]


# --- Bill suggestions from transactions (M58) ---


class BillSuggestion(BaseModel):
    merchant_key: str
    name: str
    amount: Money
    frequency: RecurringFrequency
    next_due_date: date
    occurrences: int
    last_seen: date


class BillUpdateSuggestion(BaseModel):
    """M59: an existing bill whose live charge pattern has drifted."""

    bill_id: str
    name: str
    dismiss_key: str
    current_amount: Money
    suggested_amount: Money
    frequency: RecurringFrequency
    next_due_date: date
    occurrences: int
    last_seen: date


class BillSuggestionListResponse(BaseModel):
    suggestions: list[BillSuggestion]
    updates: list[BillUpdateSuggestion]


class BillSuggestionDismissRequest(BaseModel):
    merchant_key: str = Field(min_length=1, max_length=120)


class IncomeListResponse(BaseModel):
    income: list[IncomeSource]


# --- Income analysis + tax estimate (M61) ---


class IncomeAnalysisTransaction(BaseModel):
    transaction_id: str
    occurred_at: date
    amount: Money
    name: str
    # M62 evidence details: payer as the bank reported it, the full bank
    # memo, and the checking account the deposit landed in.
    merchant: str | None = None
    description: str | None = None
    account_name: str = ""
    excluded: bool


class IncomeSourceAnalysis(BaseModel):
    source_key: str
    name: str
    # Detected cadence, or "irregular" for the manually-added group — wider
    # than RecurringFrequency on purpose.
    frequency: str
    manually_added: bool
    typical_amount: Money
    total_amount: Money
    transactions: list[IncomeAnalysisTransaction]


class IncomeRollup(BaseModel):
    annual_income: Money
    monthly_average: Money
    transaction_count: int
    window_days: int
    # M63: how far back the synced history actually goes.
    coverage_start: date | None = None
    coverage_days: int = 0


class TaxEstimate(BaseModel):
    tax_year: int
    filing_status: str
    income_treated_as_net: bool
    # M65: USPS state code; state_income_tax is None when the state is unset
    # or not modeled (an assumption line says which).
    state: str | None = None
    gross_income: Money
    net_income: Money | None = None
    standard_deduction: Money
    taxable_income: Money
    federal_income_tax: Money
    fica_tax: Money
    state_income_tax: Money | None = None
    total_tax: Money
    effective_rate: float
    assumptions: list[str]


ChatImageMediaType = Literal["image/jpeg", "image/png", "image/webp"]


# --- Compensation profiles (M73) ---


class IncomeEarner(BaseModel):
    id: str
    label: str
    base_salary: Money
    rsu_annual: Money
    rsu_frequency: Literal["monthly", "quarterly", "semiannual", "annual"] | None = None
    rsu_next_vest_date: date | None = None
    bonus_percent: float = 0.0
    bonus_month: int | None = None
    w2_year: int | None = None
    w2_wages: Money | None = None
    w2_withheld: Money | None = None


class ExpectedIncomeEvent(BaseModel):
    date: date
    label: str
    amount: Money


class IncomeProfile(BaseModel):
    earners: list[IncomeEarner]
    expected_annual_gross: Money
    expected_events: list[ExpectedIncomeEvent]


class IncomeEarnerCreateRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    base_salary_minor: int = Field(default=0, ge=0)
    rsu_annual_minor: int = Field(default=0, ge=0)
    rsu_frequency: Literal["monthly", "quarterly", "semiannual", "annual"] | None = None
    rsu_next_vest_date: date | None = None
    bonus_percent: float = Field(default=0.0, ge=0, le=100)
    bonus_month: int | None = Field(default=None, ge=1, le=12)
    w2_year: int | None = Field(default=None, ge=1990, le=2100)
    w2_wages_minor: int | None = Field(default=None, ge=0)
    w2_withheld_minor: int | None = Field(default=None, ge=0)


# M77: PDFs are accepted here (rasterized server-side) but not in chat images.
W2ScanMediaType = Literal["image/jpeg", "image/png", "image/webp", "application/pdf"]


class W2ScanRequest(BaseModel):
    image_base64: str = Field(min_length=1)
    image_media_type: W2ScanMediaType


class W2ScanResult(BaseModel):
    """Candidate values only — the user confirms before anything is saved."""

    year: int | None = None
    employer: str | None = None
    wages_minor: int | None = None
    federal_withheld_minor: int | None = None
    note: str


class IncomeAnalysisResponse(BaseModel):
    sources: list[IncomeSourceAnalysis]
    other_inflows: list[IncomeAnalysisTransaction]
    rollup: IncomeRollup
    # M63: set when the synced history does not span the full window.
    coverage_warning: str | None = None
    # M73: declared compensation; when present it is the tax authority.
    profile: IncomeProfile | None = None
    tax: TaxEstimate


class IncomeOverrideRequest(BaseModel):
    transaction_id: str
    verdict: Literal["include", "exclude", "clear"]


class IncomeTaxSettingsRequest(BaseModel):
    tax_filing_status: str = Field(min_length=1, max_length=20)
    income_treated_as_net: bool
    # M65: USPS state code for state income tax; null clears it.
    state: str | None = Field(default=None, min_length=2, max_length=2)


# --- Household memory (M57, ADR 0016) ---


class Memory(BaseModel):
    id: str
    key: str
    value: str
    source: Literal["chat", "manual"]
    created_at: datetime
    updated_at: datetime


class MemoryListResponse(BaseModel):
    memories: list[Memory]


class MemoryCreateRequest(BaseModel):
    value: str = Field(min_length=1, max_length=500)


class PurchaseAdvisorRequest(BaseModel):
    merchant: str | None = None
    item: str
    description: str | None = None
    price: Money
    source: PurchaseSource | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    user_question: str | None = None


class RetirementScenarioRequest(BaseModel):
    current_age: int = Field(ge=0, le=120)
    retirement_age: int = Field(ge=1, le=130)
    current_savings: Money
    monthly_contribution: Money
    annual_return_rate: float = Field(ge=0, le=1)
    annual_expenses: Money | None = None


class Impact(BaseModel):
    area: ImpactArea
    summary: str
    amount: Money | None = None


class Recommendation(BaseModel):
    id: str
    answer: str
    assumptions: list[str]
    impacts: list[Impact]
    tradeoffs: list[str]
    alternatives: list[str]
    confidence: float = Field(ge=0, le=1)
    calculation_refs: list[str]
    warnings: list[str] = Field(default_factory=list)
    # M25: the model id that produced this answer, or null when it came from
    # the deterministic calculation path (no AI involved).
    answered_by: str | None = None
    # M25: the vision model that read an attached photo (ADR 0011); null when
    # no photo was attached or it could not be analyzed.
    photo_described_by: str | None = None




class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)
    # Optional attached photo (ADR 0011) or PDF (M84a — the server rasterizes
    # page 1 for the vision model): base64-encoded, images downscaled client-side.
    image_base64: str | None = None
    image_media_type: W2ScanMediaType | None = None
    # Optional attached data file (M85): CSV / spreadsheet / text, base64. The
    # server builds a bounded grounded PREVIEW that joins the prompt; the file
    # is never stored and never written to records (distinct from imports).
    data_file_base64: str | None = None
    data_file_name: str | None = Field(default=None, max_length=255)


class VoiceRequest(BaseModel):
    # M87a: the advisor answer to speak. Capped to keep synthesis bounded.
    text: str = Field(min_length=1, max_length=4000)
    voice: str | None = None


class ChatResponse(BaseModel):
    conversation_id: str
    recommendation: Recommendation


class AiRuntimeConfig(BaseModel):
    provider: AiRuntimeProvider
    base_url: str
    model: str
    enabled: bool = True


class AiRuntimeStatus(BaseModel):
    """Live readiness of the household's AI runtime, for the chat UI banner."""

    enabled: bool
    provider: str
    model: str
    ready: bool
    served_model: str | None = None
    detail: str
    # Vision routing (ADR 0011): whether photos can be analyzed, and by what.
    vision_ready: bool = False
    vision_model: str | None = None
    # Whether a vision path is configured at all (distinguishes "loading" from "off").
    vision_enabled: bool = False
    # M50: what "loading" actually means, classified from the vLLM log tail.
    loading_phase: Literal["downloading", "loading", "warming_up", "error", "starting"] | None = (
        None
    )
    loading_detail: str | None = None


class AiModelInfo(BaseModel):
    """One curated model option for the runtime picker (ADR 0012, planning data)."""

    id: str
    label: str
    role: Literal["main", "vision", "both"]
    parameters_b: float
    est_memory_gb: float
    est_disk_gb: float
    tool_parser: str | None = None
    supports_vision: bool
    gated: bool
    notes: str = ""
    # M71: HF release timestamp (ISO); None for curated entries (hand-vetted,
    # treated as modern by the ranking).
    created_at: str | None = None


class AiModelCatalog(BaseModel):
    models: list[AiModelInfo]


class AiApplyRequest(BaseModel):
    """One-click model apply (ADR 0013). vision_model None disables photo analysis."""

    main_model: str
    vision_model: str | None = None


class AiSwapStatus(BaseModel):
    state: Literal["idle", "running", "succeeded", "failed", "unavailable"]
    main_model: str | None = None
    vision_model: str | None = None
    log_tail: str = ""


class AiHardwareProfile(BaseModel):
    """Best-effort hardware facts for model-fit planning (ADR 0012)."""

    gpu_memory_gb: float | None = None
    system_memory_gb: float | None = None
    disk_free_gb: float
    source: str


class ImportCreateRequest(BaseModel):
    source_type: ImportSourceType
    filename: str
    account_id: str | None = None


class ImportRecord(BaseModel):
    id: str
    source_type: ImportSourceType
    filename: str
    status: ImportStatus
    error_message: str | None = None
    skipped_row_count: int = 0
    created_at: datetime


class ImportListResponse(BaseModel):
    imports: list[ImportRecord]


class DocumentExtraction(BaseModel):
    id: str
    extraction_type: DocumentExtractionType
    text: str
    structured_fields: dict[str, Any]
    confidence: float = Field(ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    created_at: datetime


class Document(BaseModel):
    id: str
    content_type: str
    created_at: datetime
    extraction: DocumentExtraction | None = None


class DocumentListResponse(BaseModel):
    documents: list[Document]


class ReportGenerateRequest(BaseModel):
    report_type: ReportType


class GoalProgressSummary(BaseModel):
    goal_id: str
    name: str
    percent_complete: float | None = None
    months_to_completion: int | None = None
    calculation_ref: str


class ReportSummary(BaseModel):
    wins: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    unusual_spending: list[str] = Field(default_factory=list)
    recommended_actions: list[str] = Field(default_factory=list)
    goal_progress: list[GoalProgressSummary] = Field(default_factory=list)
    net_cash_flow: Money
    calculation_refs: list[str] = Field(default_factory=list)


class Report(BaseModel):
    id: str
    report_type: ReportType
    period_start: date
    period_end: date
    summary: ReportSummary
    explanation_text: str
    explanation_source: ExplanationSource
    generated_at: datetime


class ReportListResponse(BaseModel):
    reports: list[Report]


class BackupJob(BaseModel):
    id: str
    status: BackupJobStatus
    size_bytes: int | None = None
    error_message: str | None = None
    started_at: datetime
    completed_at: datetime | None = None
    pruned_at: datetime | None = None
    created_at: datetime


class BackupJobListResponse(BaseModel):
    backups: list[BackupJob]


# --- M9: household setup, data management, and audit --------------------------


class HouseholdCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=120)
    base_currency: str = Field(min_length=3, max_length=3)
    owner_email: str = Field(min_length=3)
    owner_password: str = Field(min_length=8)
    owner_display_name: str = Field(min_length=1, max_length=120)


class HouseholdUpdateRequest(BaseModel):
    """M43: household-level settings. None on the target resets to the default."""

    emergency_fund_target_months: float | None = Field(default=None, ge=1, le=60)
    # Distinguishes "reset to default" (True) from "leave unchanged" (field omitted).
    clear_emergency_fund_target: bool = False


class Member(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: HouseholdRole
    created_at: datetime


class MemberListResponse(BaseModel):
    members: list[Member]


class MemberCreateRequest(BaseModel):
    email: str = Field(min_length=3)
    password: str = Field(min_length=8)
    display_name: str = Field(min_length=1, max_length=120)
    role: HouseholdRole


class MemberRoleUpdateRequest(BaseModel):
    role: HouseholdRole


class AccountCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: AccountType
    currency: str = Field(min_length=3, max_length=3)
    annual_interest_rate: float | None = Field(default=None, ge=0)
    minimum_payment: Money | None = None


class AccountUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    type: AccountType | None = None
    annual_interest_rate: float | None = Field(default=None, ge=0)
    minimum_payment: Money | None = None
    # M36: percent XOR amount; clear_emergency_fund removes the designation.
    emergency_fund_percent: float | None = Field(default=None, ge=0, le=100)
    emergency_fund_amount: Money | None = None
    clear_emergency_fund: bool = False


class AccountBalanceCreateRequest(BaseModel):
    balance: Money


class TransactionCreateRequest(BaseModel):
    account_id: str
    occurred_at: date
    amount: Money
    merchant: str | None = None
    description: str | None = None
    category_id: str | None = None


class TransactionUpdateRequest(BaseModel):
    account_id: str | None = None
    occurred_at: date | None = None
    amount: Money | None = None
    merchant: str | None = None
    description: str | None = None
    category_id: str | None = None
    clear_category: bool = False


class BillCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount: Money
    frequency: RecurringFrequency
    account_id: str | None = None
    next_due_date: date | None = None
    category_id: str | None = None


class BillUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    amount: Money | None = None
    frequency: RecurringFrequency | None = None
    next_due_date: date | None = None
    # Present + a value sets the category; present + null clears it; absent leaves
    # it (checked via model_fields_set in the endpoint).
    category_id: str | None = None


class IncomeCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount: Money
    frequency: RecurringFrequency


class IncomeUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    amount: Money | None = None
    frequency: RecurringFrequency | None = None


class AuditEvent(BaseModel):
    id: str
    actor_user_id: str | None = None
    action: str
    entity_type: str
    entity_id: str | None = None
    summary: str
    created_at: datetime


class AuditEventListResponse(BaseModel):
    events: list[AuditEvent]


# --- M10: conversation history -----------------------------------------------

ConversationMessageRole = Literal["user", "assistant"]


class Conversation(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0


class ConversationListResponse(BaseModel):
    conversations: list[Conversation]


class ConversationMessage(BaseModel):
    id: str
    role: ConversationMessageRole
    content: str
    recommendation_id: str | None = None
    sequence: int
    created_at: datetime


class ConversationDetail(BaseModel):
    id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[ConversationMessage]


# --- M27: institution connections (ADR 0015) ---------------------------------


class ConnectionCreateRequest(BaseModel):
    provider: Literal["simplefin"] = "simplefin"
    display_name: str = Field(min_length=1, max_length=120)
    # One-time SimpleFIN setup token; exchanged immediately, never stored.
    setup_token: str = Field(min_length=8)


class InstitutionConnection(BaseModel):
    """A linked institution. The access credential is never exposed."""

    id: str
    provider: str
    display_name: str
    status: str
    last_synced_at: datetime | None = None
    last_sync_error: str | None = None
    created_at: datetime


class ConnectionListResponse(BaseModel):
    connections: list[InstitutionConnection]


class ConnectionSyncResult(BaseModel):
    accounts_synced: int
    imported: int
    duplicates_skipped: int
