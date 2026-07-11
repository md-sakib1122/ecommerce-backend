"""Payment tests: the Strategy factory and the checkout endpoint.

External provider calls are monkeypatched at each provider's private I/O boundary
(`_create_intent`, `_grant_token`, `_create`) so the strategy's own mapping and
the service's persistence stay under test — only the network is faked.
"""
from decimal import Decimal

import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.payment import Payment, PaymentProvider, PaymentStatus
from app.models.user import User
from app.payments import factory
from app.payments.bkash_provider import BkashProvider
from app.payments.factory import get_payment_strategy
from app.payments.stripe_provider import StripeProvider


class FakeStripeObj(dict):
    """Stand-in for a StripeObject: a dict with attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


# --- factory (Strategy pattern) ---------------------------------------------
def test_factory_returns_correct_strategy():
    assert isinstance(get_payment_strategy(PaymentProvider.stripe), StripeProvider)
    assert isinstance(get_payment_strategy(PaymentProvider.bkash), BkashProvider)


def test_factory_unsupported_provider_raises_400(monkeypatch):
    monkeypatch.delitem(factory._REGISTRY, PaymentProvider.bkash)
    with pytest.raises(HTTPException) as exc_info:
        get_payment_strategy(PaymentProvider.bkash)
    assert exc_info.value.status_code == 400


# --- checkout ----------------------------------------------------------------
async def test_checkout_stripe_creates_pending_payment(
    client, session, order, monkeypatch
):
    async def fake_create_intent(self, *, amount, currency, order_id):
        assert amount == 2000  # 20.00 -> smallest unit
        assert order_id == order.id
        return FakeStripeObj(
            id="pi_test_1", client_secret="pi_test_1_secret", amount=amount
        )

    monkeypatch.setattr(StripeProvider, "_create_intent", fake_create_intent)

    resp = await client.post(
        f"/api/v1/orders/{order.id}/checkout",
        json={"order_id": order.id, "provider": "stripe"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "client_secret": "pi_test_1_secret",
        "payment_intent_id": "pi_test_1",
    }

    payment = (
        await session.execute(
            select(Payment).where(Payment.transaction_id == "pi_test_1")
        )
    ).scalar_one()
    assert payment.order_id == order.id
    assert payment.provider == PaymentProvider.stripe
    assert payment.status == PaymentStatus.pending


async def test_checkout_bkash_creates_pending_payment(
    client, session, order, monkeypatch
):
    async def fake_grant(self, http_client):
        return "token-123"

    async def fake_create(self, http_client, token, order_arg, currency):
        return {
            "paymentID": "bkash_1",
            "bkashURL": "https://pay.bkash/redirect/bkash_1",
            "statusCode": "0000",
        }

    monkeypatch.setattr(BkashProvider, "_grant_token", fake_grant)
    monkeypatch.setattr(BkashProvider, "_create", fake_create)

    resp = await client.post(
        f"/api/v1/orders/{order.id}/checkout",
        json={"order_id": order.id, "provider": "bkash"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "bkash_url": "https://pay.bkash/redirect/bkash_1",
        "payment_id": "bkash_1",
    }

    payment = (
        await session.execute(
            select(Payment).where(Payment.transaction_id == "bkash_1")
        )
    ).scalar_one()
    assert payment.provider == PaymentProvider.bkash
    assert payment.status == PaymentStatus.pending


async def test_checkout_non_pending_order_conflicts(client, session, order):
    order.status = OrderStatus.paid
    session.add(order)
    await session.commit()

    resp = await client.post(
        f"/api/v1/orders/{order.id}/checkout",
        json={"order_id": order.id, "provider": "stripe"},
    )
    assert resp.status_code == 409


async def test_checkout_foreign_order_not_found(client, session, product):
    """Checking out another user's order must 404 (not leak its existence)."""
    other = User(email="other@example.com", hashed_password="x")
    session.add(other)
    await session.commit()
    await session.refresh(other)

    foreign = Order(
        user_id=other.id, status=OrderStatus.pending, total_amount=Decimal("10.00")
    )
    foreign.items = [
        OrderItem(
            product_id=product.id,
            quantity=1,
            price=Decimal("10.00"),
            subtotal=Decimal("10.00"),
        )
    ]
    session.add(foreign)
    await session.commit()
    await session.refresh(foreign)

    resp = await client.post(
        f"/api/v1/orders/{foreign.id}/checkout",
        json={"order_id": foreign.id, "provider": "stripe"},
    )
    assert resp.status_code == 404
