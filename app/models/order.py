# Order SQLModel table
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class OrderStatus(str, Enum):
    pending = "pending"
    paid = "paid"
    canceled = "canceled"


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", nullable=False, index=True)

    # Denormalized sum of OrderItems.subtotal, recomputed deterministically
    # by OrderService.calculate_totals() rather than trusted from the client.
    total_amount: Decimal = Field(
        default=Decimal("0.00"), nullable=False, max_digits=12, decimal_places=2
    )
    status: OrderStatus = Field(
        default=OrderStatus.pending, nullable=False, index=True
    )

    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        nullable=False,
        sa_column_kwargs={"onupdate": datetime.utcnow},
    )

    user: "User" = Relationship(back_populates="orders")
    items: List["OrderItem"] = Relationship(
        back_populates="order",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    payments: List["Payment"] = Relationship(back_populates="order")