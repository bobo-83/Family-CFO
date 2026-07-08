from __future__ import annotations

import logging
import os

from family_cfo_ocr_worker import (
    DeterministicOcrAdapter,
    ExtractionResult,
    PdfTextExtractionAdapter,
)
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.engine import Engine

from family_cfo_api import repository
from family_cfo_api.config import Settings
from family_cfo_api.deps import get_app_settings, get_current_session, get_engine
from family_cfo_api.schemas import Document, DocumentExtraction, DocumentListResponse, ErrorResponse

router = APIRouter(tags=["Documents"])
logger = logging.getLogger(__name__)

_pdf_adapter = PdfTextExtractionAdapter()
_ocr_adapter = DeterministicOcrAdapter()


def _extract(content: bytes, content_type: str) -> tuple[str, ExtractionResult]:
    if content_type == "application/pdf":
        return "pdf_text", _pdf_adapter.extract(content, content_type)
    if content_type.startswith("image/"):
        return "ocr", _ocr_adapter.extract(content, content_type)
    raise HTTPException(status_code=400, detail=f"Unsupported content type: {content_type}")


def _to_schema(
    document: repository.DocumentRecord, extraction: repository.DocumentExtractionRecord | None
) -> Document:
    extraction_schema = None
    if extraction is not None:
        extraction_schema = DocumentExtraction(
            id=extraction.id,
            extraction_type=extraction.extraction_type,
            text=extraction.text,
            structured_fields=extraction.structured_fields,
            confidence=extraction.confidence,
            warnings=extraction.warnings,
            created_at=extraction.created_at,
        )

    return Document(
        id=document.id,
        content_type=document.content_type,
        created_at=document.created_at,
        extraction=extraction_schema,
    )


@router.get(
    "/documents",
    operation_id="listDocuments",
    response_model=DocumentListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List uploaded documents and their extractions",
)
async def list_documents(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> DocumentListResponse:
    records = repository.list_documents_with_extractions(engine, session.household_id)
    return DocumentListResponse(
        documents=[_to_schema(document, extraction) for document, extraction in records]
    )


@router.post(
    "/documents",
    operation_id="createDocument",
    response_model=Document,
    status_code=201,
    responses={
        400: {"description": "Invalid request", "model": ErrorResponse},
        401: {"description": "Unauthorized", "model": ErrorResponse},
    },
    summary="Upload a document (receipt or PDF) for structured extraction",
)
async def create_document(
    file: UploadFile,
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
    settings: Settings = Depends(get_app_settings),
) -> Document:
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    content_type = file.content_type or "application/octet-stream"
    extraction_type, result = _extract(content, content_type)

    document_id = repository.new_id()
    safe_filename = os.path.basename(file.filename or "upload")
    storage_path = f"{document_id}/{safe_filename}"
    full_path = os.path.join(settings.import_staging_dir, "documents", storage_path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as staged_file:
        staged_file.write(content)

    document = repository.create_document(
        engine,
        household_id=session.household_id,
        content_type=content_type,
        storage_path=os.path.join("documents", storage_path),
    )
    extraction = repository.create_document_extraction(
        engine,
        document_id=document.id,
        extraction_type=extraction_type,
        text=result.text,
        structured_fields=result.structured_fields,
        confidence=result.confidence,
        warnings=result.warnings,
    )

    logger.info(
        "document extracted document_id=%s extraction_type=%s confidence=%.2f",
        document.id,
        extraction_type,
        result.confidence,
    )

    return _to_schema(document, extraction)
