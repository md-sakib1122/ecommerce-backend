# Pydantic request/response models for Order
from datetime import datetime
from decimal import Decimal
from typing import List

from pydantic import BaseModel, Field

from app.models.order import OrderStatus


class OrderItemCreate(BaseModel):
    """
    Client only sends product_id + quantity. price/subtotal are never
    trusted from the client — OrderService looks up the live Product.price
    server-side and computes both deterministically.
    """

    product_id: int
    quantity: int = Field(gt=0)


class OrderCreate(BaseModel):
    items: List[OrderItemCreate] = Field(min_length=1)


class OrderItemRead(BaseModel):
    id: int
    product_id: int
    quantity: int
    price: Decimal
    subtotal: Decimal

    class Config:
        from_attributes = True


class OrderRead(BaseModel):
    id: int
    user_id: int
    total_amount: Decimal
    status: OrderStatus
    items: List[OrderItemRead]
    created_at: datetime

    class Config:
        from_attributes = True