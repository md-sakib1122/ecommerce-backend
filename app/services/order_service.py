"""OOP business logic for orders.

`OrderService` owns checkout, retrieval, cancellation, and `mark_paid()` — the
payment-success hook the Payment System (2.1.4) calls. Keeping payment out of this
class is what lets a new provider drop in without touching order logic (Strategy
pattern seam, 2.2.4).

Money rules (server-side only, `dbrln`):
- `order_items.price` is a **snapshot** of `Product.price` at order time — never re-read live.
- `subtotal = price * quantity`; `order.total_amount = Σ subtotal` — computed by
  `calculate_totals()`, never trusted from the client.
- Stock is decremented **only** in `mark_paid()` (after payment), never at checkout.
"""
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.product import Product, ProductStatus
from app.models.user import User
from app.schemas.order import OrderCreate
from app.services.product_service import ProductService

_CENTS = Decimal("0.01")


class OrderService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- deterministic money algorithm (2.2.3) -------------------------------
    @staticmethod
    def calculate_totals(
        line_items: list[tuple[Decimal, int]],
    ) -> tuple[list[Decimal], Decimal]:
        """Pure, deterministic. Given `(unit_price, quantity)` pairs, return the
        per-line subtotals and their grand total, each quantized to 2 decimals.
        Uses `Decimal` throughout — never float."""
        subtotals = [(price * quantity).quantize(_CENTS) for price, quantity in line_items]
        total = sum(subtotals, Decimal("0.00")).quantize(_CENTS)
        return subtotals, total

    # --- lookups -------------------------------------------------------------
    async def get_order_or_404(self, order_id: int) -> Order:
        result = await self.session.execute(
            select(Order)
            .where(Order.id == order_id)
            .options(selectinload(Order.items))
        )
        order = result.scalar_one_or_none()
        if order is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order {order_id} not found.",
            )
        return order

    async def get_owned_order(self, order_id: int, user: User) -> Order:
        """Fetch an order the caller owns. A mismatch returns 404 (not 403) so we
        don't leak the existence of other users' orders."""
        order = await self.get_order_or_404(order_id)
        if order.user_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order {order_id} not found.",
            )
        return order

    # --- checkout ------------------------------------------------------------
    async def create_order(self, user_id: int, data: OrderCreate) -> Order:
        """Create a `pending` order. Prices are snapshotted from live products and
        totals computed server-side. Stock is validated (soft check) but NOT
        decremented — that happens on payment success in `mark_paid()`."""
        # Aggregate duplicate product_ids into a single line with summed quantity.
        qty_by_product: dict[int, int] = {}
        for item in data.items:
            qty_by_product[item.product_id] = (
                qty_by_product.get(item.product_id, 0) + item.quantity
            )

        result = await self.session.execute(
            select(Product).where(Product.id.in_(qty_by_product.keys()))
        )
        products = {p.id: p for p in result.scalars().all()}

        validated: list[tuple[Product, int]] = []
        for product_id, quantity in qty_by_product.items():
            product = products.get(product_id)
            if product is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Product {product_id} not found.",
                )
            if product.status != ProductStatus.active:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Product {product_id} is not available for purchase.",
                )
            if quantity > product.stock:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Insufficient stock for product {product_id} "
                        f"(have {product.stock}, requested {quantity})."
                    ),
                )
            validated.append((product, quantity))

        subtotals, total = self.calculate_totals(
            [(product.price, quantity) for product, quantity in validated]
        )

        order = Order(user_id=user_id, status=OrderStatus.pending, total_amount=total)
        order.items = [
            OrderItem(
                product_id=product.id,
                quantity=quantity,
                price=product.price,  # snapshot
                subtotal=subtotal,
            )
            for (product, quantity), subtotal in zip(validated, subtotals)
        ]
        self.session.add(order)
        await self.session.commit()
        # Re-fetch with items eagerly loaded for the OrderRead response.
        return await self.get_order_or_404(order.id)

    # --- cancel --------------------------------------------------------------
    async def cancel_order(self, order_id: int, user: User) -> Order:
        """Cancel the caller's own order. Only `pending` orders can be canceled;
        a paid order would need a refund (out of scope). Idempotent for an order
        that is already canceled."""
        order = await self.get_owned_order(order_id, user)
        if order.status == OrderStatus.canceled:
            return order
        if order.status != OrderStatus.pending:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only pending orders can be canceled.",
            )
        order.status = OrderStatus.canceled
        self.session.add(order)
        await self.session.commit()
        return await self.get_order_or_404(order.id)

    # --- payment-success hook (called by the Payment System) -----------------
    async def mark_paid(self, order_id: int) -> Order:
        """Transition `pending -> paid` and atomically reduce stock for every item,
        all in one transaction. Idempotent: replaying it on an already-paid order
        is a no-op (so a retried payment webhook never double-decrements stock).
        Raises 409 if the order is canceled or any item is out of stock."""
        order = await self.get_order_or_404(order_id)
        if order.status == OrderStatus.paid:
            return order
        if order.status == OrderStatus.canceled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot pay a canceled order.",
            )

        product_service = ProductService(self.session)
        try:
            for item in order.items:
                await product_service.reduce_stock(item.product_id, item.quantity)
            order.status = OrderStatus.paid
            self.session.add(order)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

        return await self.get_order_or_404(order.id)
