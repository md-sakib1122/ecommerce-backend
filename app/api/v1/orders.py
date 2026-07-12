"""Order routes: a user places, views, and cancels their own orders.

Checkout computes prices/totals server-side and leaves the order `pending`; stock
is only reduced once payment succeeds (Payment System, via OrderService.mark_paid).
Admin oversight and the pay/checkout endpoints are intentionally not here.
"""
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_session
from app.models.user import User
from app.schemas.order import OrderCreate, OrderRead
from app.services.order_service import OrderService

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
async def create_order(
    data: OrderCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    """Place an order for one or more products. Totals are computed server-side;
    the order starts `pending` and reserves no stock."""
    order = await OrderService(session).create_order(current_user.id, data)
    return OrderRead.model_validate(order)


@router.get("/{order_id}", response_model=OrderRead)
async def get_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    order = await OrderService(session).get_owned_order(order_id, current_user)
    return OrderRead.model_validate(order)


@router.post("/{order_id}/cancel", response_model=OrderRead)
async def cancel_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> OrderRead:
    """Cancel one of your own orders (only while it is still `pending`)."""
    order = await OrderService(session).cancel_order(order_id, current_user)
    return OrderRead.model_validate(order)
