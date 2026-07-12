"""bKash tokenized-checkout payment strategy (2.1.4).

bKash has no official SDK, so we hit the sandbox REST API directly with
`httpx.AsyncClient`. Flow: grant a token → create a payment (returns a `bkashURL`
the client is redirected to) → after the user pays, bKash redirects to our
callback → we **execute** the payment, which is the authoritative settlement.

`transaction_id` is the bKash `paymentID` (known at create time). Confirmation
always calls `execute` server-side and trusts bKash's `transactionStatus`, never
a status supplied by the (unauthenticated) callback caller.
"""
from typing import Any

import httpx
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

_TIMEOUT = httpx.Timeout(30.0)


def _map_status(transaction_status: str | None) -> PaymentStatus:
    """bKash `transactionStatus` → our normalized `PaymentStatus`."""
    if transaction_status == "Completed":
        return PaymentStatus.success
    if transaction_status == "Initiated":
        return PaymentStatus.pending
    return PaymentStatus.failed


class BkashProvider(PaymentStrategy):
    provider = PaymentProvider.bkash

    def __init__(self) -> None:
        self._base_url = settings.BKASH_BASE_URL.rstrip("/")

    # --- provider I/O boundary (monkeypatched in tests) ----------------------
    async def _grant_token(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            f"{self._base_url}/tokenized/checkout/token/grant",
            headers={
                "username": settings.BKASH_USERNAME,
                "password": settings.BKASH_PASSWORD,
                "Accept": "application/json",
            },
            json={
                "app_key": settings.BKASH_APP_KEY,
                "app_secret": settings.BKASH_APP_SECRET,
            },
        )
        resp.raise_for_status()
        return resp.json()["id_token"]

    def _auth_headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": token,
            "X-APP-Key": settings.BKASH_APP_KEY,
            "Accept": "application/json",
        }

    async def _create(
        self, client: httpx.AsyncClient, token: str, order: Order, currency: str
    ) -> dict[str, Any]:
        resp = await client.post(
            f"{self._base_url}/tokenized/checkout/create",
            headers=self._auth_headers(token),
            json={
                "mode": "0011",
                "payerReference": str(order.id),
                "callbackURL": settings.BKASH_CALLBACK_URL,
                "amount": str(order.total_amount),
                "currency": currency,
                "intent": "sale",
                "merchantInvoiceNumber": f"order-{order.id}",
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def _execute(
        self, client: httpx.AsyncClient, token: str, payment_id: str
    ) -> dict[str, Any]:
        resp = await client.post(
            f"{self._base_url}/tokenized/checkout/execute",
            headers=self._auth_headers(token),
            json={"paymentID": payment_id},
        )
        resp.raise_for_status()
        return resp.json()

    # --- strategy interface --------------------------------------------------
    async def create_payment(self, order: Order, *, currency: str) -> CheckoutResult:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            token = await self._grant_token(client)
            data = await self._create(client, token, order, currency)

        payment_id = data.get("paymentID")
        bkash_url = data.get("bkashURL")
        if not payment_id or not bkash_url:
            # statusCode != "0000" or a malformed create response.
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="bKash did not return a payment id / URL.",
            )
        return CheckoutResult(
            transaction_id=payment_id,
            raw_response=data,
            client_payload={"bkash_url": bkash_url, "payment_id": payment_id},
        )

    async def confirm(self, ctx: ConfirmationContext) -> PaymentOutcome:
        payment_id = ctx.params.get("paymentID")
        if not payment_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="bKash callback is missing paymentID.",
            )
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            token = await self._grant_token(client)
            data = await self._execute(client, token, payment_id)

        return PaymentOutcome(
            transaction_id=payment_id,
            status=_map_status(data.get("transactionStatus")),
            raw_response=data,
        )
