# OrderItem SQLModel table
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class OrderItem(SQLModel, table=True):
    """
    Line item join between Order and Product.
    `price` is a snapshot of Product.price at order-creation time (never
    re-read live from Product later) so historical orders don't silently
    change value if the product's price is edited afterward.
    `subtotal` = price * quantity, written once by OrderService.
    """

    __tablename__ = "order_items"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="orders.id", nullable=False, index=True)
    product_id: int = Field(foreign_key="products.id", nullable=False, index=True)

    quantity: int = Field(nullable=False, gt=0)
    price: Decimal = Field(nullable=False, max_digits=12, decimal_places=2)
    subtotal: Decimal = Field(nullable=False, max_digits=12, decimal_places=2)

    order: "Order" = Relationship(back_populates="items")
    product: "Product" = Relationship(back_populates="order_items")