import os

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.engine import Engine

from family_cfo_api import audit, finance_service, repository, undo_actions
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine, require_role
from family_cfo_api.schemas import ErrorResponse
from family_cfo_api.schemas import Money as MoneySchema
from family_cfo_api.schemas import (
    Transaction,
    TransactionCreateRequest,
    TransactionListResponse,
    TransactionUpdateRequest,
)

router = APIRouter(tags=["Transactions"])


def _to_schema(
    record: repository.TransactionRecord,
    account_names: dict[str, str] | None = None,
    counterparty: str | None = None,
    institutions: dict[str, str] | None = None,
) -> Transaction:
    names = account_names or {}
    return Transaction(
        id=record.id,
        account_id=record.account_id,
        occurred_at=record.occurred_at,
        amount=MoneySchema(amount_minor=record.amount_minor, currency=record.currency),
        merchant=record.merchant,
        category=record.category,
        category_id=record.category_id,
        description=record.description,
        account_name=names.get(record.account_id),
        counterparty=counterparty,
        duplicate_state=record.duplicate_state,
        external_id=record.external_id,
        institution=(institutions or {}).get(record.account_id),
        note=record.note,
        has_attachment=record.attachment_path is not None,
    )


# Two transactions are the two legs of one transfer when they sit in different
# accounts, move the opposite direction for the same amount, within a few days.
_TRANSFER_MATCH_DAYS = 3


def _counterparties(
    records: list[repository.TransactionRecord], account_names: dict[str, str]
) -> dict[str, str]:
    """For each transfer-looking transaction, the name of the account its matching
    (opposite-leg) transaction lives in — so the UI can show source → destination."""
    by_amount: dict[int, list[repository.TransactionRecord]] = {}
    for record in records:
        by_amount.setdefault(abs(record.amount_minor), []).append(record)

    result: dict[str, str] = {}
    for record in records:
        for other in by_amount.get(abs(record.amount_minor), ()):
            if (
                other.account_id != record.account_id
                and (other.amount_minor > 0) != (record.amount_minor > 0)
                and abs((other.occurred_at - record.occurred_at).days) <= _TRANSFER_MATCH_DAYS
            ):
                name = account_names.get(other.account_id)
                if name:
                    result[record.id] = name
                break
    return result


def _require_category(engine: Engine, household_id: str, category_id: str) -> None:
    if repository.get_category(engine, household_id, category_id) is None:
        raise HTTPException(status_code=404, detail="Category not found")


def _require_account(
    engine: Engine, household_id: str, account_id: str
) -> repository.AccountRecord:
    account = repository.get_account(engine, household_id, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@router.get(
    "/transactions",
    operation_id="listTransactions",
    response_model=TransactionListResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        422: {"description": "Invalid month", "model": ErrorResponse},
    },
    summary="List transactions (recent, or every one in a given YYYY-MM month)",
)
async def list_transactions(
    month: str | None = None,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> TransactionListResponse:
    if month is not None:
        # A month drill-down needs EVERY transaction in that month, not just the
        # recent 200 the default returns — otherwise older months look empty.
        import calendar
        from datetime import date as _date

        try:
            year_str, month_str = month.split("-")
            start = _date(int(year_str), int(month_str), 1)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=422, detail="month must be YYYY-MM") from exc
        end = start.replace(day=calendar.monthrange(start.year, start.month)[1])
        records = repository.list_transactions(
            engine, session.household_id, limit=100_000, start=start, end=end
        )
    else:
        records = repository.list_transactions(engine, session.household_id)
    account_names = repository.account_name_map(engine, session.household_id)
    institutions = repository.account_institution_map(engine, session.household_id)
    counterparties = _counterparties(records, account_names)
    return TransactionListResponse(
        transactions=[
            _to_schema(record, account_names, counterparties.get(record.id), institutions)
            for record in records
        ]
    )


@router.get(
    "/transactions/review",
    operation_id="listTransactionsForReview",
    response_model=TransactionListResponse,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        422: {"description": "Unknown kind", "model": ErrorResponse},
    },
    summary="Transactions to review — duplicates (default), transfers, or credits/refunds",
)
async def list_transactions_for_review(
    kind: str = "duplicates",
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> TransactionListResponse:
    account_names = repository.account_name_map(engine, session.household_id)
    institutions = repository.account_institution_map(engine, session.household_id)

    if kind == "duplicates":
        records = repository.list_transactions(
            engine, session.household_id, limit=100_000,
            duplicate_states=repository.REVIEW_DUPLICATE_STATES,
        )
    elif kind in ("transfers", "credits"):
        everything = repository.list_transactions(engine, session.household_id, limit=100_000)
        if kind == "transfers":
            records = [
                r for r in everything
                if (r.category or "").lower() in repository.TRANSFER_CATEGORY_NAMES
            ]
        else:
            # Money back that isn't a paycheck or an internal move: an inflow whose
            # category isn't Income or Transfers (a statement credit or a refund).
            skip = set(repository.INCOME_CATEGORY_NAMES) | set(repository.TRANSFER_CATEGORY_NAMES)
            records = [
                r for r in everything
                if r.amount_minor > 0 and (r.category or "").lower() not in skip
            ]
    else:
        raise HTTPException(status_code=422, detail="kind must be duplicates, transfers or credits")

    counterparties = _counterparties(records, account_names)
    return TransactionListResponse(
        transactions=[
            _to_schema(record, account_names, counterparties.get(record.id), institutions)
            for record in records
        ]
    )


@router.post(
    "/transactions",
    operation_id="createTransaction",
    response_model=Transaction,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Account not found", "model": ErrorResponse},
    },
    summary="Manually create a transaction",
)
async def create_transaction(
    payload: TransactionCreateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Transaction:
    account = _require_account(engine, session.household_id, payload.account_id)
    if payload.amount.currency != account.currency:
        raise HTTPException(status_code=400, detail=f"Amount currency must be {account.currency}")
    if payload.category_id is not None:
        _require_category(engine, session.household_id, payload.category_id)

    transaction_id = repository.create_transaction(
        engine,
        household_id=session.household_id,
        account_id=payload.account_id,
        occurred_at=payload.occurred_at,
        amount_minor=payload.amount.amount_minor,
        currency=payload.amount.currency,
        merchant=payload.merchant,
        description=payload.description,
        import_source=None,
        import_id=None,
        review_state="reviewed",
        category_id=payload.category_id,
    )
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "transaction.created",
        "transaction",
        transaction_id,
        "Created a manual transaction",
        undo_token=undo_actions.created("transaction", transaction_id),
    )
    record = repository.get_transaction(engine, session.household_id, transaction_id)
    assert record is not None
    return _to_schema(record, repository.account_name_map(engine, session.household_id))


@router.patch(
    "/transactions/{transaction_id}",
    operation_id="updateTransaction",
    response_model=Transaction,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Transaction not found", "model": ErrorResponse},
    },
    summary="Update a transaction",
)
async def update_transaction(
    transaction_id: str,
    payload: TransactionUpdateRequest,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Transaction:
    existing = repository.get_transaction(engine, session.household_id, transaction_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if payload.account_id is not None:
        _require_account(engine, session.household_id, payload.account_id)
    if payload.category_id is not None:
        _require_category(engine, session.household_id, payload.category_id)

    amount_minor = payload.amount.amount_minor if payload.amount is not None else None
    currency = payload.amount.currency if payload.amount is not None else None
    repository.update_transaction(
        engine,
        session.household_id,
        transaction_id,
        account_id=payload.account_id,
        occurred_at=payload.occurred_at,
        amount_minor=amount_minor,
        currency=currency,
        merchant=payload.merchant,
        description=payload.description,
        category_id=payload.category_id,
        clear_category=payload.clear_category,
    )
    # Categorizing one transaction files the merchant's other still-uncategorized
    # transactions too (minimize duplicate input), from whichever screen did it.
    if payload.category_id is not None and not payload.clear_category:
        finance_service.propagate_category_to_merchant(
            engine, session.household_id, transaction_id, payload.category_id
        )
    # M97: resolve a Review-queue flag (keep both / dispute).
    if payload.duplicate_state is not None:
        repository.set_transaction_duplicate_state(
            engine, session.household_id, transaction_id, payload.duplicate_state
        )
    # M100: set/clear the free-text note when the field was provided.
    if "note" in payload.model_fields_set:
        repository.set_transaction_note(
            engine, session.household_id, transaction_id, payload.note
        )
    label = existing.merchant or existing.description or "a transaction"
    summary = _describe_update(engine, session.household_id, payload, label)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "transaction.updated",
        "transaction",
        transaction_id,
        summary,
        # Every edit is reversible: restore the transaction's prior field values
        # (M110 undo-completeness). `existing` was read before the mutation.
        undo_token=undo_actions.transaction_updated(existing),
    )
    record = repository.get_transaction(engine, session.household_id, transaction_id)
    assert record is not None
    return _to_schema(record, repository.account_name_map(engine, session.household_id))


def _describe_update(
    engine: Engine,
    household_id: str,
    payload: TransactionUpdateRequest,
    label: str,
) -> str:
    """A human-readable summary for the Activity log. Every edit is undoable via a
    field-restore token built at the call site (M110), so this only names it."""
    if payload.clear_category:
        return f"Uncategorized “{label}”"
    if payload.category_id is not None:
        category = repository.get_category(engine, household_id, payload.category_id)
        name = category.name if category is not None else "a category"
        return f"Filed “{label}” under {name}"
    if payload.duplicate_state is not None:
        return f"Marked “{label}” as {payload.duplicate_state}"
    if "note" in payload.model_fields_set:
        return f"Edited the note on “{label}”"
    return f"Updated “{label}”"


@router.delete(
    "/transactions/{transaction_id}",
    operation_id="deleteTransaction",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Transaction not found", "model": ErrorResponse},
    },
    summary="Delete a transaction",
)
async def delete_transaction(
    transaction_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
) -> Response:
    existing = repository.get_transaction(engine, session.household_id, transaction_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    repository.delete_transaction(engine, session.household_id, transaction_id)
    # M97: deleting one leg of a duplicate pair should clear the flag on the leg
    # that's left (its group is no longer a duplicate).
    repository.flag_possible_duplicates(engine, session.household_id)
    label = existing.merchant or existing.description or "a transaction"
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "transaction.deleted",
        "transaction",
        transaction_id,
        f"Deleted “{label}”",
        # Reversible: re-insert the row exactly as it was (M110 undo-completeness).
        undo_token=undo_actions.transaction_deleted(existing),
    )
    return Response(status_code=204)


_ATTACH_MAX_BYTES = 15 * 1024 * 1024  # 15 MB is plenty for a phone photo

_CHECK_PROMPT = (
    "This is a photo of a paper check or a payment slip. In one short line, say what "
    "it's for: the payee (pay to the order of) and the memo/for line if present, and "
    "the amount if clearly written. Reply with just that description — no preamble, no "
    "extra words. If nothing is readable, reply exactly: unreadable"
)


def _attachment_dir(settings: Settings) -> str:
    # Under the staging dir so backups (which tar it) include attachments.
    return os.path.join(settings.import_staging_dir, "attachments")


def _parse_image_description(
    engine: Engine, household_id: str, content: bytes, content_type: str
) -> str | None:
    """Best-effort: read a short description off an attached check image with the
    on-box vision model. Returns None when no model is configured or nothing
    readable — the upload still succeeds, the user just types the note themselves."""
    if content_type not in ("image/jpeg", "image/png"):
        return None
    import base64

    from family_cfo_ai_orchestrator import RuntimeMessage, RuntimeUnavailableError

    from family_cfo_api.ai_runtime_selection import select_vision_describer

    describer, _source = select_vision_describer(engine, household_id)
    if describer is None:
        return None
    data_url = f"data:{content_type};base64,{base64.b64encode(content).decode('ascii')}"
    try:
        completion = describer.complete(
            [RuntimeMessage(role="user", content=_CHECK_PROMPT, image_data_url=data_url)],
            temperature=0.0,
            max_tokens=80,
        )
    except RuntimeUnavailableError:
        return None
    finally:
        describer.close()
    text = (completion.text or "").strip()
    if not text or text.lower() == "unreadable":
        return None
    return text[:280]


@router.put(
    "/transactions/{transaction_id}/attachment",
    operation_id="uploadTransactionAttachment",
    response_model=Transaction,
    responses={
        400: {"description": "Invalid file", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Transaction not found", "model": ErrorResponse},
        413: {"description": "File too large", "model": ErrorResponse},
    },
    summary="Attach an image to a transaction (e.g. a photo of a check)",
)
async def upload_transaction_attachment(
    transaction_id: str,
    file: UploadFile,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> Transaction:
    record = repository.get_transaction(engine, session.household_id, transaction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    content_type = file.content_type or "application/octet-stream"
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Attachment must be an image")
    content = await file.read(_ATTACH_MAX_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > _ATTACH_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the maximum allowed size")

    ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/heic": ".heic"}.get(content_type, "")
    directory = _attachment_dir(settings)
    os.makedirs(directory, exist_ok=True)
    # Replace any prior attachment for this transaction.
    for existing in os.listdir(directory):
        if existing.startswith(transaction_id):
            try:
                os.remove(os.path.join(directory, existing))
            except OSError:
                pass
    filename = f"{transaction_id}{ext}"
    with open(os.path.join(directory, filename), "wb") as out:
        out.write(content)

    repository.set_transaction_attachment(
        engine, session.household_id, transaction_id,
        os.path.join("attachments", filename), content_type,
    )
    # Parse a description off the image (a check's payee/memo) and pre-fill the
    # note — but never clobber a note the user already wrote. They can edit it.
    if not (record.note and record.note.strip()):
        parsed = _parse_image_description(engine, session.household_id, content, content_type)
        if parsed:
            repository.set_transaction_note(engine, session.household_id, transaction_id, parsed)
    attach_label = record.merchant or record.description or "a transaction"
    audit.write_audit(
        engine, session.household_id, session.user_id,
        "transaction.attachment_added", "transaction", transaction_id,
        f"Attached a photo to “{attach_label}”",
        # `record` was read before the write: restoring it clears the attachment
        # (and the auto-filled note) — M117.
        undo_token=undo_actions.transaction_updated(record),
    )
    updated = repository.get_transaction(engine, session.household_id, transaction_id)
    assert updated is not None
    return _to_schema(updated, repository.account_name_map(engine, session.household_id))


@router.get(
    "/transactions/{transaction_id}/attachment",
    operation_id="getTransactionAttachment",
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        404: {"description": "No attachment", "model": ErrorResponse},
    },
    summary="Download a transaction's attached image",
)
async def get_transaction_attachment(
    transaction_id: str,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> FileResponse:
    record = repository.get_transaction(engine, session.household_id, transaction_id)
    if record is None or not record.attachment_path:
        raise HTTPException(status_code=404, detail="No attachment")
    full_path = os.path.join(settings.import_staging_dir, record.attachment_path)
    if not os.path.isfile(full_path):
        raise HTTPException(status_code=404, detail="No attachment")
    return FileResponse(
        full_path, media_type=record.attachment_content_type or "application/octet-stream"
    )


@router.delete(
    "/transactions/{transaction_id}/attachment",
    operation_id="deleteTransactionAttachment",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Transaction not found", "model": ErrorResponse},
    },
    summary="Remove a transaction's attached image",
)
async def delete_transaction_attachment(
    transaction_id: str,
    session: repository.SessionContext = Depends(require_role("owner", "adult")),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    record = repository.get_transaction(engine, session.household_id, transaction_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if record.attachment_path:
        full_path = os.path.join(settings.import_staging_dir, record.attachment_path)
        if os.path.exists(full_path):
            os.remove(full_path)
        repository.set_transaction_attachment(engine, session.household_id, transaction_id, None, None)
    return Response(status_code=204)
