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


async def _seed_payment(
    session, order, transaction_id: str, provider=PaymentProvider.stripe
) -> Payment:
    payment = Payment(
        order_id=order.id,
        provider=provider,
        transaction_id=transaction_id,
        status=PaymentStatus.pending,
    )
    session.add(payment)
    await session.commit()
    await session.refresh(payment)
    return payment


# --- checkout error paths ------------------------------------------------------
async def test_checkout_body_url_mismatch_400(client, order):
    resp = await client.post(
        f"/api/v1/orders/{order.id}/checkout",
        json={"order_id": order.id + 1, "provider": "stripe"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Body order_id does not match the URL."


async def test_checkout_missing_order_404(client):
    resp = await client.post(
        "/api/v1/orders/9999/checkout",
        json={"order_id": 9999, "provider": "stripe"},
    )
    assert resp.status_code == 404, resp.text


async def test_checkout_invalid_provider_422(client, order):
    resp = await client.post(
        f"/api/v1/orders/{order.id}/checkout",
        json={"order_id": order.id, "provider": "paypal"},
    )
    assert resp.status_code == 422, resp.text


async def test_checkout_duplicate_transaction_id_409(
    client, session, order, monkeypatch
):
    await _seed_payment(session, order, "pi_dup")

    async def fake_create_intent(self, *, amount, currency, order_id):
        return FakeStripeObj(id="pi_dup", client_secret="pi_dup_secret", amount=amount)

    monkeypatch.setattr(StripeProvider, "_create_intent", fake_create_intent)

    resp = await client.post(
        f"/api/v1/orders/{order.id}/checkout",
        json={"order_id": order.id, "provider": "stripe"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "A payment with this transaction id already exists."


async def test_checkout_bkash_malformed_create_502(client, session, order, monkeypatch):
    async def fake_grant(self, http_client):
        return "token-123"

    async def fake_create(self, http_client, token, order_arg, currency):
        return {"statusCode": "9999"}  # no paymentID / bkashURL

    monkeypatch.setattr(BkashProvider, "_grant_token", fake_grant)
    monkeypatch.setattr(BkashProvider, "_create", fake_create)

    resp = await client.post(
        f"/api/v1/orders/{order.id}/checkout",
        json={"order_id": order.id, "provider": "bkash"},
    )
    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"] == "bKash did not return a payment id / URL."

    payments = (await session.execute(select(Payment))).scalars().all()
    assert payments == []  # nothing persisted for the failed attempt


# --- payment read --------------------------------------------------------------
async def test_get_payment_owner_200(client, session, order):
    payment = await _seed_payment(session, order, "pi_read")

    resp = await client.get(f"/api/v1/payments/{payment.id}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == payment.id
    assert body["order_id"] == order.id
    assert body["provider"] == "stripe"
    assert body["transaction_id"] == "pi_read"
    assert body["status"] == "pending"


async def test_get_payment_foreign_404(client, session, product):
    """Another user's payment must 404 (ownership is resolved via the order)."""
    other = User(email="other2@example.com", hashed_password="x")
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

    payment = await _seed_payment(session, foreign, "pi_foreign")

    resp = await client.get(f"/api/v1/payments/{payment.id}")
    assert resp.status_code == 404, resp.text


async def test_get_payment_missing_404(client):
    resp = await client.get("/api/v1/payments/9999")
    assert resp.status_code == 404, resp.text


# --- auth guard ----------------------------------------------------------------
async def test_payments_require_auth_401(anon_client):
    resp = await anon_client.post(
        "/api/v1/orders/1/checkout", json={"order_id": 1, "provider": "stripe"}
    )
    assert resp.status_code == 401, resp.text

    resp = await anon_client.get("/api/v1/payments/1")
    assert resp.status_code == 401, resp.text
