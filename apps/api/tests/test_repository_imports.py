from datetime import date

from sqlalchemy.engine import Engine

from family_cfo_api import fixtures, repository


def test_create_and_get_import(demo_engine: Engine) -> None:
    record = repository.create_import(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
        source_type="csv",
        filename="statement.csv",
    )

    assert record.status == "pending"
    assert record.retry_count == 0

    fetched = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, record.id)
    assert fetched is not None
    assert fetched.filename == "statement.csv"


def test_get_import_scoped_to_household(demo_engine: Engine) -> None:
    record = repository.create_import(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=None,
        source_type="csv",
        filename="statement.csv",
    )

    assert repository.get_import(demo_engine, "some-other-household", record.id) is None


def test_list_imports_orders_newest_first(demo_engine: Engine) -> None:
    first = repository.create_import(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, None, "csv", "first.csv"
    )
    second = repository.create_import(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, None, "csv", "second.csv"
    )

    records = repository.list_imports(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)

    assert [r.id for r in records][:2] == [second.id, first.id]


def test_create_and_get_import_file(demo_engine: Engine) -> None:
    import_record = repository.create_import(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, None, "csv", "statement.csv"
    )

    repository.create_import_file(
        demo_engine,
        import_id=import_record.id,
        storage_path=f"{import_record.id}/statement.csv",
        content_type="text/csv",
        size_bytes=42,
    )

    file_record = repository.get_import_file(demo_engine, import_record.id)
    assert file_record is not None
    assert file_record.size_bytes == 42


def test_update_import_status_and_increment_retry(demo_engine: Engine) -> None:
    import_record = repository.create_import(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, None, "csv", "statement.csv"
    )

    repository.update_import_status(demo_engine, import_record.id, status="processing")
    fetched = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, import_record.id)
    assert fetched is not None
    assert fetched.status == "processing"

    new_count = repository.increment_import_retry_count(demo_engine, import_record.id)
    assert new_count == 1
    new_count = repository.increment_import_retry_count(demo_engine, import_record.id)
    assert new_count == 2


def test_list_processable_imports_requires_a_file(demo_engine: Engine) -> None:
    without_file = repository.create_import(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, None, "csv", "no-file.csv"
    )
    with_file = repository.create_import(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, None, "csv", "has-file.csv"
    )
    repository.create_import_file(
        demo_engine, import_id=with_file.id, storage_path="x", content_type="text/csv", size_bytes=1
    )

    processable = repository.list_processable_imports(demo_engine)
    processable_ids = {record.id for record, _file in processable}

    assert with_file.id in processable_ids
    assert without_file.id not in processable_ids


def test_transaction_exists_detects_matching_row(demo_engine: Engine) -> None:
    assert not repository.transaction_exists(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        fixtures.DEMO_CHECKING_ACCOUNT_ID,
        date(2026, 1, 1),
        -1234,
    )

    repository.create_transaction(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
        occurred_at=date(2026, 1, 1),
        amount_minor=-1234,
        currency="USD",
        merchant="Test Merchant",
        description=None,
        import_source="csv",
        import_id=None,
        review_state="pending",
    )

    assert repository.transaction_exists(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        fixtures.DEMO_CHECKING_ACCOUNT_ID,
        date(2026, 1, 1),
        -1234,
    )


def test_apply_import_marks_pending_transactions_reviewed(demo_engine: Engine) -> None:
    import_record = repository.create_import(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        fixtures.DEMO_CHECKING_ACCOUNT_ID,
        "csv",
        "statement.csv",
    )
    repository.create_transaction(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
        occurred_at=date(2026, 1, 1),
        amount_minor=-500,
        currency="USD",
        merchant="Coffee Shop",
        description=None,
        import_source="csv",
        import_id=import_record.id,
        review_state="pending",
    )

    updated_count = repository.apply_import(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, import_record.id
    )

    assert updated_count == 1
    applied = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, import_record.id)
    assert applied is not None
    assert applied.status == "completed"


def test_discard_import_deletes_pending_transactions(demo_engine: Engine) -> None:
    import_record = repository.create_import(
        demo_engine,
        fixtures.DEMO_HOUSEHOLD_ID,
        fixtures.DEMO_CHECKING_ACCOUNT_ID,
        "csv",
        "statement.csv",
    )
    repository.create_transaction(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        account_id=fixtures.DEMO_CHECKING_ACCOUNT_ID,
        occurred_at=date(2026, 1, 1),
        amount_minor=-500,
        currency="USD",
        merchant="Coffee Shop",
        description=None,
        import_source="csv",
        import_id=import_record.id,
        review_state="pending",
    )

    deleted_count = repository.discard_import(
        demo_engine, fixtures.DEMO_HOUSEHOLD_ID, import_record.id
    )

    assert deleted_count == 1
    discarded = repository.get_import(demo_engine, fixtures.DEMO_HOUSEHOLD_ID, import_record.id)
    assert discarded is not None
    assert discarded.status == "discarded"

    remaining = repository.list_transactions(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert all(t.id != "" for t in remaining)  # sanity: query still works
    assert not any(t.merchant == "Coffee Shop" for t in remaining)


def test_create_document_and_extraction(demo_engine: Engine) -> None:
    document = repository.create_document(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        content_type="application/pdf",
        storage_path="docs/statement.pdf",
    )
    extraction = repository.create_document_extraction(
        demo_engine,
        document_id=document.id,
        extraction_type="pdf_text",
        text="Statement text",
        structured_fields={"possible_amounts": ["$10.00"]},
        confidence=0.4,
        warnings=["possible_amounts is a naive regex match, not a validated total"],
    )

    assert extraction.document_id == document.id

    results = repository.list_documents_with_extractions(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)
    assert len(results) == 1
    listed_document, listed_extraction = results[0]
    assert listed_document.id == document.id
    assert listed_extraction is not None
    assert listed_extraction.text == "Statement text"


def test_list_documents_with_extractions_handles_no_extraction(demo_engine: Engine) -> None:
    document = repository.create_document(
        demo_engine,
        household_id=fixtures.DEMO_HOUSEHOLD_ID,
        content_type="image/png",
        storage_path="docs/receipt.png",
    )

    results = repository.list_documents_with_extractions(demo_engine, fixtures.DEMO_HOUSEHOLD_ID)

    assert len(results) == 1
    listed_document, listed_extraction = results[0]
    assert listed_document.id == document.id
    assert listed_extraction is None
