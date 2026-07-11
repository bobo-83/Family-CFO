"""Institution connections and transaction sync (M27, ADR 0015).

The `BankConnector` protocol keeps providers pluggable; `SimpleFINConnector`
is the first implementation. Bank credentials never touch this server — the
user connects their bank at SimpleFIN Bridge and pastes a one-time setup token
here, which is exchanged for a read-only access URL. That access URL is itself
a credential, so it is Fernet-encrypted at rest and never returned or logged.

Dedupe is two-tier: provider transaction ids give hard idempotency (unique
(account_id, external_id)); a content hash is the soft fallback used by flows
without provider ids (CSV).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Protocol

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.config import Settings

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 30.0

# How much transaction history every sync requests. Matches the bill-detection
# lookback (bill_detection.LOOKBACK_DAYS) so recurring-charge suggestions have
# a full ~13 months to work with.
SYNC_LOOKBACK_DAYS = 400


class BankSyncError(RuntimeError):
    """A sync/claim failure with a user-safe message (no credentials inside)."""


# --- credential encryption -----------------------------------------------------


def encrypt_credential(settings: Settings, value: str) -> str:
    key = settings.backup_encryption_key
    if not key:
        raise BankSyncError(
            "FAMILY_CFO_BACKUP_ENCRYPTION_KEY must be set before linking an institution"
        )
    try:
        return Fernet(key.encode()).encrypt(value.encode()).decode()
    except (ValueError, TypeError) as exc:
        raise BankSyncError("invalid encryption key") from exc


def decrypt_credential(settings: Settings, token: str) -> str:
    key = settings.backup_encryption_key
    if not key:
        raise BankSyncError("encryption key is not configured")
    try:
        return Fernet(key.encode()).decrypt(token.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise BankSyncError("stored credential could not be decrypted") from exc


# --- dedupe --------------------------------------------------------------------


def compute_import_hash(
    account_id: str, occurred_at: date, amount_minor: int, payee: str | None
) -> str:
    """Content hash for provider-id-less rows (ADR 0015 soft dedupe)."""
    normalized = (payee or "").strip().lower()
    raw = f"{account_id}|{occurred_at.isoformat()}|{amount_minor}|{normalized}"
    return hashlib.sha256(raw.encode()).hexdigest()


# --- account type inference (M35) ----------------------------------------------

# SimpleFIN carries no account-type field, so infer conservatively from the
# account name when a connection account is first auto-created. Order matters:
# first match wins; anything unmatched stays "checking". Existing accounts are
# never retyped — manual corrections from the Accounts page stick.
_ACCOUNT_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("retirement", re.compile(r"\b(?:401\s*\(?k\)?|403\s*\(?b\)?|457|ira|roth|sep|pension|tsp|retirement)\b", re.I)),
    ("hsa", re.compile(r"\b(?:hsa|health\s+savings)\b", re.I)),
    ("529", re.compile(r"\b(?:529|college\s+savings|education\s+savings)\b", re.I)),
    ("brokerage", re.compile(r"\b(?:brokerage|investment|investing)\b", re.I)),
    ("mortgage", re.compile(r"\bmortgage\b", re.I)),
    ("auto_loan", re.compile(r"\b(?:auto|car|vehicle)\s+loan\b", re.I)),
    ("student_loan", re.compile(r"\bstudent\s+loans?\b", re.I)),
    ("credit_card", re.compile(r"\b(?:credit\s*card|visa|mastercard|amex|american\s+express)\b", re.I)),
    ("savings", re.compile(r"\b(?:savings|money\s+market|mma)\b", re.I)),
)


def infer_account_type(name: str) -> str:
    """Best-effort account type from a provider account name; defaults to checking."""
    for account_type, pattern in _ACCOUNT_TYPE_PATTERNS:
        if pattern.search(name):
            return account_type
    return "checking"


# --- connector seam ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExternalTransaction:
    external_id: str
    occurred_at: date
    amount_minor: int
    payee: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class ExternalAccount:
    external_id: str
    name: str
    currency: str
    balance_minor: int | None
    transactions: list[ExternalTransaction] = field(default_factory=list)


class BankConnector(Protocol):
    """A provider that can exchange a setup token and fetch account data."""

    def claim(self, setup_token: str) -> str: ...

    def fetch_accounts(self, access_url: str, since: date | None) -> list[ExternalAccount]: ...


def _decimal_to_minor(value: str) -> int:
    try:
        return int((Decimal(value) * 100).to_integral_value())
    except (InvalidOperation, ValueError) as exc:
        raise BankSyncError(f"provider returned a non-numeric amount: {value!r}") from exc


class SimpleFINConnector:
    """SimpleFIN protocol: setup token -> access URL -> GET /accounts."""

    def __init__(self, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(timeout=_TIMEOUT_SECONDS)

    def claim(self, setup_token: str) -> str:
        try:
            claim_url = base64.b64decode(setup_token.strip(), validate=True).decode()
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            raise BankSyncError("that does not look like a SimpleFIN setup token") from exc
        if not claim_url.startswith("https://"):
            raise BankSyncError("setup token must decode to an https claim URL")
        try:
            response = self._client.post(claim_url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise BankSyncError("could not exchange the setup token (already used?)") from exc
        access_url = response.text.strip()
        if not access_url.startswith("https://"):
            raise BankSyncError("provider returned an invalid access URL")
        return access_url

    def fetch_accounts(self, access_url: str, since: date | None) -> list[ExternalAccount]:
        params = {}
        if since is not None:
            params["start-date"] = str(
                int(datetime(since.year, since.month, since.day, tzinfo=timezone.utc).timestamp())
            )
        try:
            response = self._client.get(f"{access_url.rstrip('/')}/accounts", params=params)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise BankSyncError("institution fetch failed") from exc

        accounts: list[ExternalAccount] = []
        for item in payload.get("accounts", []):
            transactions = [
                ExternalTransaction(
                    external_id=str(txn["id"]),
                    occurred_at=datetime.fromtimestamp(int(txn["posted"]), tz=timezone.utc).date(),
                    amount_minor=_decimal_to_minor(str(txn["amount"])),
                    payee=txn.get("payee") or None,
                    description=txn.get("description") or None,
                )
                for txn in item.get("transactions", [])
                if txn.get("id") and txn.get("posted") is not None
            ]
            accounts.append(
                ExternalAccount(
                    external_id=str(item["id"]),
                    name=str(item.get("name") or "Linked account"),
                    currency=str(item.get("currency") or "USD")[:3].upper(),
                    balance_minor=(
                        _decimal_to_minor(str(item["balance"])) if item.get("balance") else None
                    ),
                    transactions=transactions,
                )
            )
        return accounts


# --- sync service ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyncResult:
    accounts_synced: int
    imported: int
    duplicates_skipped: int


def sync_connection(
    engine: Engine,
    settings: Settings,
    connection: repository.InstitutionConnectionRecord,
    connector: BankConnector | None = None,
) -> SyncResult:
    """Fetch and import all accounts/transactions for one connection, deduped."""
    connector = connector or SimpleFINConnector()
    access_url = decrypt_credential(settings, connection.access_url_encrypted)
    # M59: always request the full detection lookback, never just "since last
    # sync" — the first sync used to send NO start-date (providers then return
    # almost nothing), so history was never fetched. The M27 external_id
    # dedupe makes re-fetching this window idempotent, so every sync also
    # self-heals gaps.
    since = date.today() - timedelta(days=SYNC_LOOKBACK_DAYS)

    try:
        external_accounts = connector.fetch_accounts(access_url, since)
    except BankSyncError as exc:
        repository.record_connection_sync(
            engine, connection.id, error=str(exc)
        )
        raise

    imported = 0
    duplicates = 0
    for ext in external_accounts:
        account_id = repository.get_or_create_connection_account(
            engine,
            household_id=connection.household_id,
            connection_id=connection.id,
            external_account_id=ext.external_id,
            name=ext.name,
            currency=ext.currency,
            account_type=infer_account_type(ext.name),
        )
        if ext.balance_minor is not None:
            repository.record_account_balance(engine, account_id, ext.balance_minor)
        for txn in ext.transactions:
            created = repository.create_transaction_deduped(
                engine,
                household_id=connection.household_id,
                account_id=account_id,
                occurred_at=txn.occurred_at,
                amount_minor=txn.amount_minor,
                currency=ext.currency,
                merchant=txn.payee,
                description=txn.description,
                import_source="bank_sync",
                external_id=txn.external_id,
            )
            if created:
                imported += 1
            else:
                duplicates += 1

    repository.record_connection_sync(engine, connection.id, error=None)
    logger.info(
        "bank sync completed connection_id=%s accounts=%s imported=%s duplicates=%s",
        connection.id,
        len(external_accounts),
        imported,
        duplicates,
    )
    return SyncResult(
        accounts_synced=len(external_accounts), imported=imported, duplicates_skipped=duplicates
    )
