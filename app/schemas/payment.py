# Pydantic request/response models for Payment
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel

from app.models.payment import PaymentProvider, PaymentStatus


class PaymentInitiate(BaseModel):
    """Body for POST /orders/{order_id}/checkout — user picks a provider."""

    order_id: int
    provider: PaymentProvider


class PaymentRead(BaseModel):
    id: int
    order_id: int
    provider: PaymentProvider
    transaction_id: str
    status: PaymentStatus
    created_at: datetime

    class Config:
        from_attributes = True


class StripeCheckoutResponse(BaseModel):
    """Returned to the client so it can complete the Stripe client-side confirm step."""

    client_secret: str
    payment_intent_id: str


class BkashCheckoutResponse(BaseModel):
    """Returned to the client so it can redirect the user into bKash's flow."""

    bkash_url: str
    payment_id: str


class StripeWebhookPayload(BaseModel):
    id: str
    type: str
    data: Dict[str, Any]


class BkashWebhookPayload(BaseModel):
    paymentID: str
    status: str
    trxID: Optional[str] = None