# Pydantic request/response models for Product
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel, Field

from app.models.product import ProductStatus


class ProductCreate(BaseModel):
    name: str = Field(max_length=255)
    sku: str = Field(max_length=64)
    description: Optional[str] = None
    price: Decimal = Field(gt=0)
    stock: int = Field(ge=0, default=0)
    status: ProductStatus = ProductStatus.active
    category_id: Optional[int] = None


class ProductUpdate(BaseModel):
    """All fields optional — admin PATCH endpoint."""

    name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = None
    price: Optional[Decimal] = Field(default=None, gt=0)
    stock: Optional[int] = Field(default=None, ge=0)
    status: Optional[ProductStatus] = None
    category_id: Optional[int] = None


class ProductRead(BaseModel):
    id: int
    name: str
    sku: str
    description: Optional[str]
    price: Decimal
    stock: int
    status: ProductStatus
    category_id: Optional[int]
    created_at: datetime

    class Config:
        from_attributes = True


class CategoryCreate(BaseModel):
    name: str = Field(max_length=150)
    parent_id: Optional[int] = None


class CategoryRead(BaseModel):
    id: int
    name: str
    parent_id: Optional[int]

    class Config:
        from_attributes = True


class CategoryTreeNode(CategoryRead):
    """Recursive shape returned by the cached DFS category tree endpoint."""

    children: List["CategoryTreeNode"] = []


CategoryTreeNode.model_rebuild()