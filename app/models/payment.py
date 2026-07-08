# Payment SQLModel table
from datetime import datetime
from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy import JSON, Column
from sqlmodel import Field, Relationship, SQLModel


class PaymentProvider(str, Enum):
    stripe = "stripe"
    bkash = "bkash"


class PaymentStatus(str, Enum):
    pending = "pending"
    success = "success"
    failed = "failed"


class Payment(SQLModel, table=True):
    """
    One row per payment attempt/provider transaction. transaction_id is
    unique so a provider webhook can be matched (and safely retried /
    de-duplicated) with a single indexed lookup, regardless of provider.
    """

    __tablename__ = "payments"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="orders.id", nullable=False, index=True)
    provider: PaymentProvider = Field(nullable=False, index=True)
    transaction_id: str = Field(nullable=False, unique=True, index=True, max_length=255)
    status: PaymentStatus = Field(
        default=PaymentStatus.pending, nullable=False, index=True
    )

    # Full provider response (payment intent / bKash execute response),
    # kept for audits and webhook replay/debugging.
    # Generic sqlalchemy.JSON (not Postgres JSONB) so tests/conftest.py can
    # still run against SQLite; Postgres will store it as `json`, which is
    # fine here since we don't query *inside* raw_response.
    raw_response: Optional[Dict[str, Any]] = Field(
        default=None, sa_column=Column(JSON)
    )

    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        nullable=False,
        sa_column_kwargs={"onupdate": datetime.utcnow},
    )

    order: "Order" = Relationship(back_populates="payments")