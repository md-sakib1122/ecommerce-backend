"""Category routes: public tree/browse reads + admin-managed CRUD.

Anyone can read the category list, the cached DFS tree, and the products under a
category branch; only admins create/update/delete categories. Writes invalidate
the Redis-cached tree (handled inside `CategoryService`).
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.db.session import get_session
from app.models.user import User
from app.schemas.category import (
    CategoryCreate,
    CategoryRead,
    CategoryTreeNode,
    CategoryUpdate,
)
from app.schemas.product import ProductRead
from app.services.category_service import CategoryService

router = APIRouter(prefix="/categories", tags=["categories"])


# --- public reads -----------------------------------------------------------
@router.get("", response_model=list[CategoryRead])
async def list_categories(
    skip: int = 0,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[CategoryRead]:
    """Flat list of categories (paginated)."""
    categories = await CategoryService(session).list_categories(skip=skip, limit=limit)
    return [CategoryRead.model_validate(c) for c in categories]


# Declared before `/{category_id}` so "tree" isn't captured as a path param.
@router.get("/tree", response_model=list[CategoryTreeNode])
async def get_category_tree(
    root_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[CategoryTreeNode]:
    """DFS-rendered category tree (Redis-cached). Pass `?root_id=` for one subtree."""
    return await CategoryService(session).get_tree(root_id=root_id)


@router.get("/{category_id}", response_model=CategoryRead)
async def get_category(
    category_id: int,
    session: AsyncSession = Depends(get_session),
) -> CategoryRead:
    category = await CategoryService(session).get_category_or_404(category_id)
    return CategoryRead.model_validate(category)


@router.get("/{category_id}/products", response_model=list[ProductRead])
async def list_category_products(
    category_id: int,
    skip: int = 0,
    limit: int = 100,
    session: AsyncSession = Depends(get_session),
) -> list[ProductRead]:
    """Active products in this category and every descendant category (DFS branch)."""
    products = await CategoryService(session).list_products_in_branch(
        category_id, skip=skip, limit=limit
    )
    return [ProductRead.model_validate(p) for p in products]


# --- admin-only management --------------------------------------------------
@router.post("", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
async def create_category(
    data: CategoryCreate,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> CategoryRead:
    category = await CategoryService(session).create(data)
    return CategoryRead.model_validate(category)


@router.patch("/{category_id}", response_model=CategoryRead)
async def update_category(
    category_id: int,
    data: CategoryUpdate,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> CategoryRead:
    category = await CategoryService(session).update(category_id, data)
    return CategoryRead.model_validate(category)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(
    category_id: int,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Delete a category. Returns 409 if it still has subcategories or products."""
    await CategoryService(session).delete(category_id)
