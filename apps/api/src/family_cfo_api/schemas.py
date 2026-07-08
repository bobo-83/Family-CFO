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
GoalType = Literal["emergency_fund", "vacation", "retirement", "college", "vehicle", "renovation", "other"]
RecurringFrequency = Literal["weekly", "biweekly", "semimonthly", "monthly", "quarterly", "annual"]
PurchaseSource = Literal["manual", "mobile_vision", "receipt", "product_photo"]
ImpactArea = Literal["cash_flow", "emergency_fund", "debt", "savings_goal", "retirement", "net_worth", "other"]
AiRuntimeProvider = Literal["vllm", "ollama", "llama_cpp", "openai_compatible"]
ImportSourceType = Literal["csv", "pdf", "ofx", "qfx"]
ImportStatus = Literal["pending", "processing", "needs_review", "completed", "discarded", "failed"]
DocumentExtractionType = Literal["pdf_text", "ocr"]


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
