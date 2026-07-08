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


class PairedDevice(BaseModel):
    id: str
    name: str
    created_at: datetime
    last_seen_at: datetime | None = None
    revoked_at: datetime | None = None


class PairedDeviceListResponse(BaseModel):
    devices: list[PairedDevice]


class HouseholdContext(BaseModel):
    household_id: str
    display_name: str
    currency: str
    net_worth: Money
    emergency_fund_months: float | None


class Account(BaseModel):
    id: str
    name: str
    type: AccountType
    balance: Money
    annual_interest_rate: float | None = None
    minimum_payment: Money | None = None


class Transaction(BaseModel):
    id: str
    account_id: str
    occurred_at: date
    amount: Money
    merchant: str | None = None
    category: str | None = None
    description: str | None = None


class Bill(BaseModel):
    id: str
    name: str
    amount: Money
    frequency: RecurringFrequency
    next_due_date: date | None = None
    account_id: str | None = None


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


class IncomeListResponse(BaseModel):
    income: list[IncomeSource]


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


class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    conversation_id: str
    recommendation: Recommendation


class AiRuntimeConfig(BaseModel):
    provider: AiRuntimeProvider
    base_url: str
    model: str
    enabled: bool = True


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


class AccountBalanceCreateRequest(BaseModel):
    balance: Money


class TransactionCreateRequest(BaseModel):
    account_id: str
    occurred_at: date
    amount: Money
    merchant: str | None = None
    description: str | None = None


class TransactionUpdateRequest(BaseModel):
    account_id: str | None = None
    occurred_at: date | None = None
    amount: Money | None = None
    merchant: str | None = None
    description: str | None = None


class BillCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    amount: Money
    frequency: RecurringFrequency
    account_id: str | None = None
    next_due_date: date | None = None


class BillUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    amount: Money | None = None
    frequency: RecurringFrequency | None = None
    next_due_date: date | None = None


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
