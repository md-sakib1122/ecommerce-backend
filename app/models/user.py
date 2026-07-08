# User SQLModel table
from datetime import datetime
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


class User(SQLModel, table=True):
    """
    Users table.
    Email is unique + indexed since login and uniqueness checks both hit it.
    """

    __tablename__ = "users"

    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(nullable=False, unique=True, index=True, max_length=255)
    hashed_password: str = Field(nullable=False)
    full_name: Optional[str] = Field(default=None, max_length=255)
    is_admin: bool = Field(default=False, nullable=False)
    is_active: bool = Field(default=True, nullable=False)

    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        nullable=False,
        sa_column_kwargs={"onupdate": datetime.utcnow},
    )

    # One user -> many orders. Orders are never deleted when a user is
    # deleted in most e-commerce systems (financial record), so no cascade
    # delete here — handle deactivation instead of hard deletes at the
    # service layer.
    orders: List["Order"] = Relationship(back_populates="user")