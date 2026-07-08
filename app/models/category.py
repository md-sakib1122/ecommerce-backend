# Category SQLModel table
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class Category(SQLModel, table=True):
    """
    Self-referential adjacency-list tree (parent_id -> categories.id).
    CategoryService.get_tree() DFS-traverses this and caches the resulting
    tree in Redis, keyed by root id, invalidated on any category write.
    """

    __tablename__ = "categories"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(nullable=False, max_length=150, index=True)
    # NULL parent_id == root category
    parent_id: Optional[int] = Field(
        default=None, foreign_key="categories.id", index=True
    )

    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        nullable=False,
        sa_column_kwargs={"onupdate": datetime.utcnow},
    )

    parent: Optional["Category"] = Relationship(
        back_populates="children",
        sa_relationship_kwargs={"remote_side": "Category.id"},
    )
    children: List["Category"] = Relationship(back_populates="parent")
    products: List["Product"] = Relationship(back_populates="category")