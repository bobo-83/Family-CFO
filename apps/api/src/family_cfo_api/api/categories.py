from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.engine import Engine

from family_cfo_api import audit, repository, rights, undo_actions
from family_cfo_api.deps import get_current_session, get_engine, require_right
from family_cfo_api.schemas import (
    Category,
    CategoryCreateRequest,
    CategoryListResponse,
    CategoryUpdateRequest,
    ErrorResponse,
)

router = APIRouter(tags=["Categories"])


def _to_schema(record: repository.CategoryRecord) -> Category:
    return Category(id=record.id, name=record.name)


@router.get(
    "/categories",
    operation_id="listCategories",
    response_model=CategoryListResponse,
    responses={401: {"description": "Unauthorized", "model": ErrorResponse}},
    summary="List spending categories",
)
async def list_categories(
    session: repository.SessionContext = Depends(get_current_session),
    engine: Engine = Depends(get_engine),
) -> CategoryListResponse:
    records = repository.list_categories(engine, session.household_id)
    return CategoryListResponse(categories=[_to_schema(r) for r in records])


@router.post(
    "/categories",
    operation_id="createCategory",
    response_model=Category,
    status_code=201,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        409: {"description": "Category name already exists", "model": ErrorResponse},
    },
    summary="Create a spending category",
)
async def create_category(
    payload: CategoryCreateRequest,
    session: repository.SessionContext = Depends(require_right(rights.CATEGORIES_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Category:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name is required")
    if repository.category_name_exists(engine, session.household_id, name):
        raise HTTPException(status_code=409, detail="A category with that name already exists")

    record = repository.create_category(engine, session.household_id, name)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "category.created",
        "category",
        record.id,
        f"Created category '{name}'",
        undo_token=undo_actions.created("category", record.id),
    )
    return _to_schema(record)


@router.patch(
    "/categories/{category_id}",
    operation_id="updateCategory",
    response_model=Category,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Category not found", "model": ErrorResponse},
        409: {"description": "Category name already exists", "model": ErrorResponse},
    },
    summary="Rename a spending category",
)
async def update_category(
    category_id: str,
    payload: CategoryUpdateRequest,
    session: repository.SessionContext = Depends(require_right(rights.CATEGORIES_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Category:
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name is required")
    existing = repository.get_category(engine, session.household_id, category_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Category not found")
    if name.lower() != existing.name.lower() and repository.category_name_exists(
        engine, session.household_id, name
    ):
        raise HTTPException(status_code=409, detail="A category with that name already exists")

    repository.update_category(engine, session.household_id, category_id, name)
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "category.updated",
        "category",
        category_id,
        f"Renamed category to '{name}'",
        undo_token=undo_actions.category_updated(existing),
    )
    return Category(id=category_id, name=name)


@router.delete(
    "/categories/{category_id}",
    operation_id="deleteCategory",
    status_code=204,
    responses={
        401: {"description": "Unauthorized", "model": ErrorResponse},
        403: {"description": "Role does not permit this action", "model": ErrorResponse},
        404: {"description": "Category not found", "model": ErrorResponse},
    },
    summary="Delete a spending category (un-categorizes its transactions)",
)
async def delete_category(
    category_id: str,
    session: repository.SessionContext = Depends(require_right(rights.CATEGORIES_MANAGE)),
    engine: Engine = Depends(get_engine),
) -> Response:
    existing = repository.get_category(engine, session.household_id, category_id)
    if not repository.delete_category(engine, session.household_id, category_id):
        raise HTTPException(status_code=404, detail="Category not found")
    name = existing.name if existing is not None else "a category"
    audit.write_audit(
        engine,
        session.household_id,
        session.user_id,
        "category.deleted",
        "category",
        category_id,
        f"Deleted category “{name}”",
        undo_token=undo_actions.category_deleted(existing) if existing is not None else None,
    )
    return Response(status_code=204)
