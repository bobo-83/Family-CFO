"""M34: OFX/QFX parsing + FITID-based idempotent imports."""

from family_cfo_api import fixtures, import_processing, ofx_parsing, repository

_OFX = b"""OFXHEADER:100
DATA:OFXSGML

<OFX>
<BANKMSGSRSV1><STMTTRNRS><STMTRS><BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260701120000[0:GMT]
<TRNAMT>-42.50
<FITID>2026070101
<NAME>GROCERY STORE
<MEMO>Weekly shop
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260702
<TRNAMT>1,500.00
<FITID>2026070202
<NAME>EMPLOYER PAYROLL
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>garbage
<TRNAMT>-1.00
<FITID>bad-row
</STMTTRN>
</BANKTRANLIST></STMTRS></STMTTRNRS></BANKMSGSRSV1>
</OFX>
"""


def test_parser_reads_sgml_blocks_and_skips_bad_ones() -> None:
    transactions, skipped = ofx_parsing.parse_ofx_transactions(_OFX)
    assert skipped == 1
    assert [t.fitid for t in transactions] == ["2026070101", "2026070202"]
    grocery, payroll = transactions
    assert grocery.amount_minor == -4250 and grocery.name == "GROCERY STORE"
    assert grocery.memo == "Weekly shop"
    assert payroll.amount_minor == 150_000
    assert str(grocery.posted) == "2026-07-01"


def _stage_ofx(engine, staging_dir: str, filename: str):
    account = repository.list_account_balances(engine, fixtures.DEMO_HOUSEHOLD_ID)[0]
    record = repository.create_import(
        engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=account.account_id,
        source_type="ofx",
        filename=filename,
    )
    path = f"{record.id}/{filename}"
    import os

    os.makedirs(os.path.join(staging_dir, record.id), exist_ok=True)
    with open(os.path.join(staging_dir, path), "wb") as handle:
        handle.write(_OFX)
    repository.create_import_file(
        engine,
        import_id=record.id,
        storage_path=path,
        content_type="application/x-ofx",
        size_bytes=len(_OFX),
    )
    return record


def test_ofx_import_creates_pending_transactions_and_is_idempotent(
    demo_engine, tmp_path
) -> None:
    staging = str(tmp_path)
    first = _stage_ofx(demo_engine, staging, "a.ofx")
    import_processing.run_pending_imports_once(demo_engine, staging)

    txns = [
        t
        for t in repository.list_transactions(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
        if t.merchant in {"GROCERY STORE", "EMPLOYER PAYROLL"}
    ]
    assert len(txns) == 2

    # Re-import the exact same file: FITIDs make it a no-op.
    second = _stage_ofx(demo_engine, staging, "b.ofx")
    import_processing.run_pending_imports_once(demo_engine, staging)
    txns = [
        t
        for t in repository.list_transactions(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
        if t.merchant in {"GROCERY STORE", "EMPLOYER PAYROLL"}
    ]
    assert len(txns) == 2
    refreshed = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, second.id)
    assert refreshed.status == "needs_review"
    assert refreshed.skipped_row_count == 3  # 1 bad block + 2 duplicates
    assert first.id != second.id


def test_pdf_statement_lines_become_pending_transactions() -> None:
    text = """ACME BANK STATEMENT
07/03 STARBUCKS COFFEE 4.50
2026-07-04  Rent payment  $1,200.00-
07/05 REFUND FROM STORE (25.00)
Some narrative line without an amount
Total fees this period
"""
    rows = import_processing._parse_statement_lines(text)
    assert len(rows) == 3
    assert rows[0][1] == "STARBUCKS COFFEE" and rows[0][2] == 450
    assert rows[1][2] == -120_000
    assert rows[2][2] == -2500
