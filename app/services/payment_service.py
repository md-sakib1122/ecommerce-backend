"""OOP business logic for payments (2.1.4 / 2.1.6).

`PaymentService` orchestrates the payment flow and owns ALL persistence and
idempotency; the concrete provider is resolved through the Strategy factory
(`app.payments.factory.get_payment_strategy`), so this class — and the order flow
behind it — never branch on Stripe vs. bKash.

Two responsibilities:
- `initiate_checkout`: validate the order, ask the strategy to start a payment,
  and persist a `pending` `Payment` keyed by the provider `transaction_id`.
- `confirm`: settle an inbound provider confirmation, dedupe on `transaction_id`
  (the idempotency key), and on success delegate to `OrderService.mark_paid()`
  which flips the order to `paid` and atomically reduces stock.
"""
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.config import settings
from app.models.order import Order, OrderStatus
from app.models.payment import Payment, PaymentProvider, PaymentStatus
from app.models.user import User
from app.payments.base import CheckoutResult, ConfirmationContext
from app.payments.factory import get_payment_strategy
from app.services.order_service import OrderService


class PaymentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- lookups -------------------------------------------------------------
    async def _get_by_transaction_id(self, transaction_id: str) -> Payment | None:
        result = await self.session.execute(
            select(Payment).where(Payment.transaction_id == transaction_id)
        )
        return result.scalar_one_or_none()

    async def get_payment(self, payment_id: int, user: User) -> Payment:
        """Fetch a payment the caller owns (ownership is enforced via the order).
        A mismatch returns 404 so we don't leak other users' payments."""
        payment = await self.session.get(Payment, payment_id)
        if payment is not None:
            order = await self.session.get(Order, payment.order_id)
            if order is not None and order.user_id == user.id:
                return payment
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Payment {payment_id} not found.",
        )

    # --- checkout ------------------------------------------------------------
    async def initiate_checkout(
        self, order_id: int, provider: PaymentProvider, user: User
    ) -> CheckoutResult:
        """Start a payment attempt for the caller's `pending` order via `provider`.
        Records a `pending` Payment row keyed by the provider transaction id and
        returns the provider's client-facing payload."""
        order = await OrderService(self.session).get_owned_order(order_id, user)
        if order.status != OrderStatus.pending:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Order {order_id} is not awaiting payment "
                    f"(status={order.status.value})."
                ),
            )

        strategy = get_payment_strategy(provider)
        result = await strategy.create_payment(
            order, currency=settings.DEFAULT_CURRENCY
        )

        payment = Payment(
            order_id=order.id,
            provider=provider,
            transaction_id=result.transaction_id,
            status=PaymentStatus.pending,
            raw_response=result.raw_response,
        )
        self.session.add(payment)
        try:
            await self.session.commit()
        except IntegrityError:
            # transaction_id is unique — a concurrent/duplicate attempt lost the race.
            await self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A payment with this transaction id already exists.",
            )
        return result

    # --- confirmation (webhook / callback) -----------------------------------
    async def confirm(
        self, provider: PaymentProvider, ctx: ConfirmationContext
    ) -> Payment:
        """Settle an inbound confirmation. The strategy verifies/executes and
        returns a normalized outcome; we dedupe on `transaction_id` and, on
        success, mark the order paid. Idempotent: replaying a confirmation for an
        already-successful payment is a no-op, so stock never double-decrements."""
        outcome = await get_payment_strategy(provider).confirm(ctx)

        payment = await self._get_by_transaction_id(outcome.transaction_id)
        if payment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No payment found for transaction {outcome.transaction_id}.",
            )
        if payment.status == PaymentStatus.success:
            return payment  # already settled — replay no-op

        payment.status = outcome.status
        payment.raw_response = outcome.raw_response
        self.session.add(payment)
        # Persist the payment result first so it is durable even if mark_paid
        # conflicts (e.g. the order was canceled meanwhile); a retried webhook
        # then short-circuits on the `success` check above.
        await self.session.commit()
        await self.session.refresh(payment)

        if outcome.status == PaymentStatus.success:
            await OrderService(self.session).mark_paid(payment.order_id)

        return payment
