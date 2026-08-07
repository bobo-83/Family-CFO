"""Per-household data export (#189) — the family's data, in boring formats.

A portable zip a household's owner can take anywhere: CSVs for the ledger,
JSON for the advisor history, and the original document/attachment files.
Everything flows through the standard repository readers, so sealed content
decrypts through the household's own key path — a sealed household that is
LOCKED cannot be exported (HouseholdLockedError → 423), which is the
guarantee working, not a failure.
"""

from __future__ import annotations

import csv
import io
import json
import os
import zipfile
from datetime import date

from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.config import Settings

_README = """Family CFO data export
======================

- accounts.csv        your accounts and latest balances
- transactions.csv    every transaction (amounts in minor units, e.g. cents)
- bills.csv           recurring bills
- income.csv          recurring income sources
- goals.csv           savings goals
- savings.csv         recurring savings contributions you declared
- categories.csv      your category list
- conversations.json  every advisor conversation, in order
- memories.json       facts the advisor remembered
- documents/          uploaded statements and scans
- attachments/        photos attached to transactions

Amounts are integers in the currency's minor unit. Dates are ISO-8601.
"""


def _csv_bytes(header: list[str], rows: list[list]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue().encode()


# #10 phase 4: the one long prose document the server authors for a reader.
# File NAMES stay English — they are the actual entries in the zip, and a
# translated description beside an untranslated filename is what a person
# needs to match them up.
_README_VI = """Family CFO — xuất dữ liệu
=========================

- accounts.csv        tài khoản của bạn và số dư mới nhất
- transactions.csv    mọi giao dịch (số tiền theo đơn vị nhỏ, ví dụ xu)
- bills.csv           hóa đơn định kỳ
- income.csv          nguồn thu nhập định kỳ
- goals.csv           mục tiêu tiết kiệm
- savings.csv         các khoản đóng góp tiết kiệm bạn đã khai báo
- categories.csv      danh sách danh mục của bạn
- conversations.json  mọi cuộc trò chuyện với cố vấn, theo thứ tự
- memories.json       những điều cố vấn đã ghi nhớ
- documents/          sao kê và ảnh chụp đã tải lên
- attachments/        ảnh đính kèm giao dịch

Số tiền là số nguyên theo đơn vị nhỏ của tiền tệ. Ngày theo chuẩn ISO-8601.
"""

_README_LT = """Family CFO — duomenų eksportas
==============================

- accounts.csv        jūsų sąskaitos ir naujausi likučiai
- transactions.csv    visos operacijos (sumos mažaisiais vienetais, pvz. centais)
- bills.csv           pasikartojančios sąskaitos
- income.csv          pasikartojantys pajamų šaltiniai
- goals.csv           taupymo tikslai
- savings.csv         jūsų deklaruoti taupymo įnašai
- categories.csv      jūsų kategorijų sąrašas
- conversations.json  visi pokalbiai su patarėju, iš eilės
- memories.json       ką patarėjas įsiminė
- documents/          įkeltos ataskaitos ir nuskaitymai
- attachments/        prie operacijų pridėtos nuotraukos

Sumos yra sveikieji skaičiai valiutos mažaisiais vienetais. Datos — ISO-8601.
"""

_READMES = {"vi": _README_VI, "lt": _README_LT}


def readme_for(language: str | None) -> str:
    from family_cfo_api import localization

    return _READMES.get(localization.normalize(language), _README)


def build_export_zip(
    engine: Engine,
    settings: Settings,
    household_id: str,
    out_path: str,
    *,
    language: str | None = None,
) -> dict[str, int]:
    """Write the export zip to out_path; returns per-section counts."""
    counts: dict[str, int] = {}

    balances = repository.list_account_balances(engine, household_id)
    account_names = {b.account_id: b.name for b in balances}
    transactions = repository.list_transactions(engine, household_id, limit=1_000_000)
    bills = repository.list_bills(engine, household_id)
    income = repository.list_income_sources(engine, household_id)
    goals = repository.list_goals(engine, household_id)
    categories = repository.list_categories(engine, household_id)
    category_names = {c.id: c.name for c in categories}
    contributions = repository.list_savings_contributions(engine, household_id)
    memories = repository.list_household_memories(engine, household_id)
    conversations = repository.list_all_household_conversations(engine, household_id)

    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("README.txt", readme_for(language))

        bundle.writestr(
            "accounts.csv",
            _csv_bytes(
                ["name", "type", "currency", "balance_minor"],
                [[b.name, b.account_type, b.currency, b.balance_minor] for b in balances],
            ),
        )
        counts["accounts"] = len(balances)

        bundle.writestr(
            "transactions.csv",
            _csv_bytes(
                ["date", "amount_minor", "currency", "merchant", "description",
                 "category", "account", "note"],
                [
                    [
                        t.occurred_at.isoformat(), t.amount_minor, t.currency,
                        t.merchant or "", t.description or "",
                        category_names.get(t.category_id, t.category or ""),
                        account_names.get(t.account_id, ""), t.note or "",
                    ]
                    for t in transactions
                ],
            ),
        )
        counts["transactions"] = len(transactions)

        bundle.writestr(
            "bills.csv",
            _csv_bytes(
                ["name", "amount_minor", "currency", "frequency", "next_due_date"],
                [
                    [b.name, b.amount_minor, b.currency, b.frequency,
                     b.next_due_date.isoformat() if b.next_due_date else ""]
                    for b in bills
                ],
            ),
        )
        counts["bills"] = len(bills)

        bundle.writestr(
            "income.csv",
            _csv_bytes(
                ["name", "amount_minor", "currency", "frequency"],
                [[i.name, i.amount_minor, i.currency, i.frequency] for i in income],
            ),
        )
        counts["income"] = len(income)

        bundle.writestr(
            "goals.csv",
            _csv_bytes(
                ["name", "type", "target_minor", "current_minor", "currency", "target_date"],
                [
                    [g.name, g.goal_type, g.target_minor, g.current_minor, g.currency,
                     g.target_date.isoformat() if g.target_date else ""]
                    for g in goals
                ],
            ),
        )
        counts["goals"] = len(goals)

        # Declared contributions are typed in by hand and exist nowhere else —
        # losing them on export would lose work the family actually did.
        bundle.writestr(
            "savings.csv",
            _csv_bytes(
                ["source_account", "destination_account", "amount_minor", "currency",
                 "frequency"],
                [
                    [
                        account_names.get(c.source_account_id, c.source_account_id),
                        account_names.get(
                            c.destination_account_id, c.destination_account_id
                        ),
                        c.amount_minor,
                        c.currency,
                        c.frequency,
                    ]
                    for c in contributions
                ],
            ),
        )
        counts["savings_contributions"] = len(contributions)

        bundle.writestr(
            "categories.csv",
            _csv_bytes(["name"], [[c.name] for c in categories]),
        )
        counts["categories"] = len(categories)

        conversation_dump = []
        message_total = 0
        for conversation in conversations:
            messages = repository.list_conversation_messages(engine, conversation.id)
            message_total += len(messages)
            conversation_dump.append(
                {
                    "title": conversation.title,
                    "created_at": conversation.created_at.isoformat(),
                    "messages": [
                        {"role": m.role, "content": m.content,
                         "at": m.created_at.isoformat()}
                        for m in messages
                    ],
                }
            )
        bundle.writestr("conversations.json", json.dumps(conversation_dump, indent=2))
        counts["messages"] = message_total

        bundle.writestr(
            "memories.json",
            json.dumps(
                [{"key": m.key, "value": m.value, "updated_at": m.updated_at.isoformat()}
                 for m in memories],
                indent=2,
            ),
        )
        counts["memories"] = len(memories)

        files_added = 0
        for document, _extraction in repository.list_documents_with_extractions(
            engine, household_id
        ):
            full = os.path.join(
                settings.import_staging_dir, "documents", document.storage_path
            )
            if os.path.isfile(full):
                bundle.write(full, arcname=f"documents/{os.path.basename(document.storage_path)}")
                files_added += 1
        counts["document_files"] = files_added

        attachment_dir = os.path.join(settings.import_staging_dir, "attachments")
        attachments_added = 0
        if os.path.isdir(attachment_dir):
            transaction_ids = {t.id for t in transactions}
            for name in os.listdir(attachment_dir):
                stem = name.rsplit(".", 1)[0]
                if stem in transaction_ids:
                    bundle.write(
                        os.path.join(attachment_dir, name), arcname=f"attachments/{name}"
                    )
                    attachments_added += 1
        counts["attachment_files"] = attachments_added

    return counts


def export_filename() -> str:
    return f"family-cfo-export-{date.today().isoformat()}.zip"
