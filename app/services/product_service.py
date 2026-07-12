"""OOP business logic for products.

`ProductService` owns the Product Management feature: admin create/update/delete
plus the public list/detail read queries. It mirrors `UserService` — a plain
class constructed with an `AsyncSession`, raising `HTTPException` for 404/409 so
routers stay thin.

Note: deletion is a **soft delete** (status -> inactive) to preserve order history
(order_items references products). `reduce_stock()` is the atomic, oversell-safe
decrement called by the order/payment flow after a successful payment.
"""
from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.product import Product, ProductStatus
from app.schemas.product import ProductCreate, ProductUpdate


class ProductService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- lookups -------------------------------------------------------------
    async def get_by_id(self, product_id: int) -> Product | None:
        return await self.session.get(Product, product_id)

    async def get_by_sku(self, sku: str) -> Product | None:
        result = await self.session.execute(select(Product).where(Product.sku == sku))
        return result.scalar_one_or_none()

    async def get_product_or_404(self, product_id: int) -> Product:
        product = await self.get_by_id(product_id)
        if product is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Product {product_id} not found.",
            )
        return product

    # --- reads ---------------------------------------------------------------
    async def list_products(
        self,
        skip: int = 0,
        limit: int = 100,
        status: ProductStatus | None = None,
        category_id: int | None = None,
        search: str | None = None,
    ) -> list[Product]:
        """List products with optional filters. A `None` status returns every
        status; the storefront default (active-only) is applied by the endpoint."""
        query = select(Product)
        if status is not None:
            query = query.where(Product.status == status)
        if category_id is not None:
            query = query.where(Product.category_id == category_id)
        if search:
            query = query.where(Product.name.ilike(f"%{search}%"))
        query = query.order_by(Product.id).offset(skip).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    # --- admin writes --------------------------------------------------------
    async def create(self, data: ProductCreate) -> Product:
        """Create a product. `sku` must be unique (409 otherwise)."""
        if await self.get_by_sku(data.sku) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A product with this sku already exists.",
            )

        product = Product(**data.model_dump())
        self.session.add(product)
        try:
            await self.session.commit()
        except IntegrityError:
            # Lost the race on the unique sku, or category_id points nowhere.
            await self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Product violates a uniqueness or reference constraint "
                "(check sku / category_id).",
            )
        await self.session.refresh(product)
        return product

    async def update(self, product_id: int, data: ProductUpdate) -> Product:
        """Partial update. Only fields the client sent are applied; `sku` is
        immutable (absent from `ProductUpdate`)."""
        product = await self.get_product_or_404(product_id)

        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(product, field, value)

        self.session.add(product)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Product violates a reference constraint "
                "(check category_id).",
            )
        await self.session.refresh(product)
        return product

    async def delete(self, product_id: int) -> None:
        """Soft delete: mark inactive so order history / FKs stay intact.
        Idempotent — an already-inactive product simply stays inactive."""
        product = await self.get_product_or_404(product_id)
        product.status = ProductStatus.inactive
        self.session.add(product)
        await self.session.commit()

    # --- stock (called by the order flow after a successful payment) ---------
    async def reduce_stock(self, product_id: int, quantity: int) -> None:
        """Atomically decrement stock, guarding against overselling with a
        `WHERE stock >= quantity` predicate so concurrent payments can never
        drive stock negative.

        Does NOT commit — `OrderService.mark_paid()` owns the transaction so
        every item's reduction and the order's status flip commit together.
        Raises 404 (product gone) or 409 (insufficient stock)."""
        result = await self.session.execute(
            update(Product)
            .where(Product.id == product_id, Product.stock >= quantity)
            .values(stock=Product.stock - quantity)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount == 0:
            # Guard failed: either the product is gone or stock is too low.
            product = await self.get_by_id(product_id)
            if product is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Product {product_id} not found.",
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Insufficient stock for product {product_id} "
                    f"(have {product.stock}, need {quantity})."
                ),
            )
