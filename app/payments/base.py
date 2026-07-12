"""Strategy pattern interface for payment providers (2.1.4 / 2.2.4).

One provider = one `PaymentStrategy` subclass. `PaymentService` and the order
flow depend ONLY on this abstract interface (resolved through
`app.payments.factory.get_payment_strategy`), so adding a provider is a new
subclass plus one factory entry — no change to order logic.

Strategies are *pure provider I/O*: they translate between our domain objects and
the provider's API and never touch the database. All persistence, idempotency,
and the `OrderService.mark_paid()` hook live in `PaymentService`.

Data crossing the seam is normalized into three small dataclasses so the service
layer never has to branch on the provider:

- `CheckoutResult` — the outcome of starting a payment (what to persist + what to
  hand back to the client).
- `PaymentOutcome` — the normalized result of confirming/settling a payment.
- `ConfirmationContext` — a uniform inbound-confirmation envelope; each provider
  reads only the fields it needs (Stripe: `raw_body` + `signature`; bKash:
  `params["paymentID"]`).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.models.order import Order
from app.models.payment import PaymentProvider, PaymentStatus


@dataclass(slots=True)
class CheckoutResult:
    """Result of `create_payment` — everything the caller needs after initiating.

    `transaction_id` is the provider reference we persist as the (unique)
    idempotency key. `client_payload` is the provider-specific dict the router
    maps to `StripeCheckoutResponse` / `BkashCheckoutResponse`.
    """

    transaction_id: str
    raw_response: dict[str, Any]
    client_payload: dict[str, Any]


@dataclass(slots=True)
class PaymentOutcome:
    """Normalized result of confirming/settling a payment attempt."""

    transaction_id: str
    status: PaymentStatus
    raw_response: dict[str, Any]


@dataclass(slots=True)
class ConfirmationContext:
    """Uniform inbound-confirmation envelope handed to `confirm`.

    Different providers confirm differently, so the router populates whichever
    fields apply and each strategy reads only what it needs:
    - Stripe verifies a signed webhook → uses `raw_body` + `signature`.
    - bKash executes a redirect callback → uses `params["paymentID"]`.
    """

    raw_body: bytes = b""
    signature: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


class PaymentStrategy(ABC):
    """Abstract payment provider. Subclasses implement `create_payment` and
    `confirm`; `provider` identifies which `PaymentProvider` enum they handle."""

    provider: ClassVar[PaymentProvider]

    @abstractmethod
    async def create_payment(self, order: Order, *, currency: str) -> CheckoutResult:
        """Start a payment attempt with the provider for `order` and return the
        transaction id, raw provider response, and the client-facing payload."""
        raise NotImplementedError

    @abstractmethod
    async def confirm(self, ctx: ConfirmationContext) -> PaymentOutcome:
        """Verify/settle an inbound confirmation and return its normalized
        `transaction_id`, final `status`, and raw provider response."""
        raise NotImplementedError
