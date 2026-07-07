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

