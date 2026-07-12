# Pydantic request/response models for Category
from typing import List, Optional

from pydantic import BaseModel, Field


class CategoryCreate(BaseModel):
    name: str = Field(max_length=150)
    parent_id: Optional[int] = None


class CategoryUpdate(BaseModel):
    """All fields optional — admin PATCH endpoint.

    Fields are applied via `model_dump(exclude_unset=True)`, so passing
    `parent_id: null` explicitly re-parents to a root, while omitting it
    leaves the parent unchanged.
    """

    name: Optional[str] = Field(default=None, max_length=150)
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
