"""Strategy factory — selects a `PaymentStrategy` by `PaymentProvider` enum.

This registry is the single extension point for the Strategy pattern: a new
provider is a new `PaymentStrategy` subclass plus one entry here. `PaymentService`
imports only `get_payment_strategy`, so order logic never learns about concrete
providers.
"""
from fastapi import HTTPException, status

from app.models.payment import PaymentProvider
from app.payments.base import PaymentStrategy
from app.payments.bkash_provider import BkashProvider
from app.payments.stripe_provider import StripeProvider

_REGISTRY: dict[PaymentProvider, type[PaymentStrategy]] = {
    PaymentProvider.stripe: StripeProvider,
    PaymentProvider.bkash: BkashProvider,
}


def get_payment_strategy(provider: PaymentProvider) -> PaymentStrategy:
    """Return a fresh strategy instance for `provider` (400 if unsupported)."""
    strategy_cls = _REGISTRY.get(provider)
    if strategy_cls is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported payment provider: {provider}.",
        )
    return strategy_cls()
