# Product SQLModel table
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class ProductStatus(str, Enum):
    active = "active"
    inactive = "inactive"


class Product(SQLModel, table=True):
    __tablename__ = "products"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(nullable=False, max_length=255, index=True)
    sku: str = Field(nullable=False, unique=True, index=True, max_length=64)
    description: Optional[str] = Field(default=None)

    # Decimal, never float — avoids binary floating point rounding on money.
    price: Decimal = Field(nullable=False, max_digits=12, decimal_places=2)
    stock: int = Field(default=0, nullable=False, ge=0)
    status: ProductStatus = Field(
        default=ProductStatus.active, nullable=False, index=True
    )

    category_id: Optional[int] = Field(
        default=None, foreign_key="categories.id", index=True
    )

    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        nullable=False,
        sa_column_kwargs={"onupdate": datetime.utcnow},
    )

    category: Optional["Category"] = Relationship(back_populates="products")
    order_items: List["OrderItem"] = Relationship(back_populates="product")