"""OFX/QFX statement parsing (M34).

A deliberately tolerant regex parser that handles both OFX 1.x (SGML, no
closing tags) and 2.x (XML) by matching `<TAG>value` pairs inside STMTTRN
blocks — no new dependencies, no strict schema. Every transaction's FITID is
the bank's own id, which feeds the M27 `external_id` hard-dedupe: re-importing
the same OFX (or overlapping exports) is idempotent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_STMTTRN = re.compile(r"<STMTTRN>(.*?)(?:</STMTTRN>|(?=<STMTTRN>)|\Z)", re.S | re.I)


def _tag(block: str, name: str) -> str | None:
    match = re.search(rf"<{name}>\s*([^<\r\n]+)", block, re.I)
    return match.group(1).strip() if match else None


@dataclass(frozen=True, slots=True)
class OfxTransaction:
    fitid: str
    posted: date
    amount_minor: int
    name: str | None
    memo: str | None


def _parse_date(raw: str) -> date | None:
    digits = re.sub(r"\D.*$", "", raw)  # strip timezone suffixes like [0:GMT]
    if len(digits) < 8:
        return None
    try:
        return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
    except ValueError:
        return None


def _parse_amount_minor(raw: str) -> int | None:
    cleaned = raw.strip().replace(" ", "")
    if "," in cleaned and "." not in cleaned:
        cleaned = cleaned.replace(",", ".")  # European decimal comma
    else:
        cleaned = cleaned.replace(",", "")  # thousands separators
    try:
        return int((Decimal(cleaned) * 100).to_integral_value(rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def parse_ofx_transactions(content: bytes) -> tuple[list[OfxTransaction], int]:
    """(transactions, skipped_block_count). Blocks missing FITID/date/amount are skipped."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("latin-1", errors="replace")

    transactions: list[OfxTransaction] = []
    skipped = 0
    for block in _STMTTRN.findall(text):
        fitid = _tag(block, "FITID")
        posted_raw = _tag(block, "DTPOSTED")
        amount_raw = _tag(block, "TRNAMT")
        posted = _parse_date(posted_raw) if posted_raw else None
        amount_minor = _parse_amount_minor(amount_raw) if amount_raw else None
        if not fitid or posted is None or amount_minor is None:
            skipped += 1
            continue
        transactions.append(
            OfxTransaction(
                fitid=fitid,
                posted=posted,
                amount_minor=amount_minor,
                name=_tag(block, "NAME"),
                memo=_tag(block, "MEMO"),
            )
        )
    return transactions, skipped
