"""Product routes: admin-managed CRUD + public catalog reads.

Admins create/update/delete (soft) products; anyone can browse the catalog.
The public list defaults to active-only; pass `?status=inactive` to see the rest.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin
from app.db.session import get_session
from app.models.product import ProductStatus
from app.models.user import User
from app.schemas.product import ProductCreate, ProductRead, ProductUpdate
from app.services.category_service import CategoryService
from app.services.product_service import ProductService

router = APIRouter(prefix="/products", tags=["products"])


# --- public catalog reads ---------------------------------------------------
@router.get("", response_model=list[ProductRead])
async def list_products(
    skip: int = 0,
    limit: int = 100,
    status: ProductStatus = ProductStatus.active,
    category_id: int | None = None,
    search: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ProductRead]:
    """Browse products. Defaults to active-only; `?status=inactive` overrides.
    Optional `category_id` and `search` (name substring) filters."""
    products = await ProductService(session).list_products(
        skip=skip,
        limit=limit,
        status=status,
        category_id=category_id,
        search=search,
    )
    return [ProductRead.model_validate(p) for p in products]


@router.get("/{product_id}", response_model=ProductRead)
async def get_product(
    product_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProductRead:
    product = await ProductService(session).get_product_or_404(product_id)
    return ProductRead.model_validate(product)


@router.get("/{product_id}/recommendations", response_model=list[ProductRead])
async def recommend_products(
    product_id: int,
    limit: int = 10,
    session: AsyncSession = Depends(get_session),
) -> list[ProductRead]:
    """Related products, found by DFS over the product's category branch."""
    products = await CategoryService(session).recommend_for_product(
        product_id, limit=limit
    )
    return [ProductRead.model_validate(p) for p in products]


# --- admin-only management --------------------------------------------------
@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
async def create_product(
    data: ProductCreate,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> ProductRead:
    product = await ProductService(session).create(data)
    return ProductRead.model_validate(product)


@router.patch("/{product_id}", response_model=ProductRead)
async def update_product(
    product_id: int,
    data: ProductUpdate,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> ProductRead:
    product = await ProductService(session).update(product_id, data)
    return ProductRead.model_validate(product)


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft delete — marks the product inactive (row and order history retained)."""
    await ProductService(session).delete(product_id)
