"""Stripe payment strategy (2.1.4).

Flow: create a PaymentIntent → hand `client_secret` to the client so it confirms
card-side → Stripe POSTs a signed webhook we verify and settle on.

`transaction_id` is the PaymentIntent id (`pi_...`), which we already know at
create time, so the pending `Payment` row is keyed by it and the webhook just
looks it up.
"""
import json
from typing import Any

import stripe
from fastapi import HTTPException, status

from app.core.config import settings
from app.models.order import Order
from app.models.payment import PaymentProvider, PaymentStatus
from app.payments.base import (
    CheckoutResult,
    ConfirmationContext,
    PaymentOutcome,
    PaymentStrategy,
)

# Stripe event types we care about → normalized status. Anything else (e.g.
# `payment_intent.created`, `payment_intent.processing`) stays `pending`.
_EVENT_STATUS: dict[str, PaymentStatus] = {
    "payment_intent.succeeded": PaymentStatus.success,
    "payment_intent.payment_failed": PaymentStatus.failed,
}


def _to_jsonable(obj: Any) -> dict[str, Any]:
    """StripeObject subclasses `dict`; round-trip through json to get a plain,
    JSON-column-safe dict (nested StripeObjects included)."""
    return json.loads(json.dumps(obj, default=str))


class StripeProvider(PaymentStrategy):
    provider = PaymentProvider.stripe

    def __init__(self) -> None:
        # Async SDK client so the network call doesn't block the event loop.
        self._client = stripe.StripeClient(settings.STRIPE_SECRET_KEY)

    # --- provider I/O boundary (monkeypatched in tests) ----------------------
    async def _create_intent(self, *, amount: int, currency: str, order_id: int) -> Any:
        return await self._client.payment_intents.create_async(
            {
                "amount": amount,
                "currency": currency,
                "metadata": {"order_id": str(order_id)},
                "automatic_payment_methods": {"enabled": True},
            }
        )

    def _construct_event(self, raw_body: bytes, signature: str | None) -> Any:
        return stripe.Webhook.construct_event(
            raw_body, signature, settings.STRIPE_WEBHOOK_SECRET
        )

    # --- strategy interface --------------------------------------------------
    async def create_payment(self, order: Order, *, currency: str) -> CheckoutResult:
        # Stripe amounts are in the smallest currency unit (e.g. poisha for BDT).
        amount = int((order.total_amount * 100).to_integral_value())
        intent = await self._create_intent(
            amount=amount, currency=currency.lower(), order_id=order.id
        )
        return CheckoutResult(
            transaction_id=intent.id,
            raw_response=_to_jsonable(intent),
            client_payload={
                "client_secret": intent.client_secret,
                "payment_intent_id": intent.id,
            },
        )

    async def confirm(self, ctx: ConfirmationContext) -> PaymentOutcome:
        try:
            event = self._construct_event(ctx.raw_body, ctx.signature)
        except Exception:
            # Bad signature or malformed payload — never trust it.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid Stripe webhook signature.",
            )

        intent = event["data"]["object"]
        return PaymentOutcome(
            transaction_id=intent["id"],
            status=_EVENT_STATUS.get(event["type"], PaymentStatus.pending),
            raw_response=_to_jsonable(event),
        )
