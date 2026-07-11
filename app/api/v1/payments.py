"""Payment routes: checkout (initiate) + provider confirmations (2.1.4 / 2.1.6).

Thin, like every router here — each endpoint parses a schema and delegates to
`PaymentService`. Which provider runs is decided by the Strategy factory inside
the service, so these handlers never mention Stripe or bKash by class.

Auth model:
- Checkout and the payment read require the logged-in owner.
- The webhook/callback endpoints are called by the *providers*, not the user, so
  they are unauthenticated and instead verified provider-side: Stripe by webhook
  signature, bKash by executing the payment server-side (never trusting a
  client-supplied status).
"""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse
from app.core.config import settings
from app.api.deps import get_current_user
from app.db.session import get_session
from app.models.payment import PaymentProvider
from app.models.user import User
from app.payments.base import ConfirmationContext
from app.schemas.payment import (
    BkashCheckoutResponse,
    BkashWebhookPayload,
    PaymentInitiate,
    PaymentRead,
    StripeCheckoutResponse,
)
from app.services.payment_service import PaymentService

router = APIRouter(tags=["payments"])


@router.post(
    "/orders/{order_id}/checkout",
    response_model=StripeCheckoutResponse | BkashCheckoutResponse,
)
async def checkout(
    order_id: int,
    data: PaymentInitiate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> StripeCheckoutResponse | BkashCheckoutResponse:
    """Initiate payment for one of your `pending` orders with the chosen provider.
    Returns the provider-specific payload the client needs to finish paying
    (Stripe `client_secret`, or the bKash redirect URL)."""
    if data.order_id != order_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Body order_id does not match the URL.",
        )

    result = await PaymentService(session).initiate_checkout(
        order_id, data.provider, current_user
    )
    if data.provider == PaymentProvider.stripe:
        return StripeCheckoutResponse(**result.client_payload)
    return BkashCheckoutResponse(**result.client_payload)


@router.post("/payments/stripe/webhook")
async def stripe_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict[str, bool]:
    """Stripe payment webhook. The raw body + `Stripe-Signature` header are
    verified inside the strategy; on `payment_intent.succeeded` the order is
    marked paid and stock reduced. Safe to retry (idempotent)."""
    ctx = ConfirmationContext(
        raw_body=await request.body(),
        signature=request.headers.get("stripe-signature"),
    )
    await PaymentService(session).confirm(PaymentProvider.stripe, ctx)
    return {"received": True}


# @router.post("/payments/bkash/callback", response_model=PaymentRead)
# async def bkash_callback(
#     payload: BkashWebhookPayload,
#     session: AsyncSession = Depends(get_session),
# ) -> PaymentRead:
#     """bKash redirect callback. We execute the payment server-side (the
#     authoritative settlement) using the `paymentID`; a `Completed` execution
#     marks the order paid and reduces stock. Idempotent on replay."""
#     ctx = ConfirmationContext(params={"paymentID": payload.paymentID})
#     payment = await PaymentService(session).confirm(PaymentProvider.bkash, ctx)
#     return PaymentRead.model_validate(payment)

@router.get("/payments/bkash/callback")
async def bkash_callback(
    payload: BkashWebhookPayload = Depends(),
    session: AsyncSession = Depends(get_session),
):
    ctx = ConfirmationContext(params={"paymentID": payload.paymentID})
    payment = await PaymentService(session).confirm(PaymentProvider.bkash, ctx)
    redirect_url = f"{settings.FRONTEND_URL}/orders/result?status={payment.status.value}&paymentID={payload.paymentID}"
    print(redirect_url)
    return RedirectResponse(url=redirect_url, status_code=303)



@router.get("/payments/{payment_id}", response_model=PaymentRead)
async def get_payment(
    payment_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> PaymentRead:
    payment = await PaymentService(session).get_payment(payment_id, current_user)
    return PaymentRead.model_validate(payment)
