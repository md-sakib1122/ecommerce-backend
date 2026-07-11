"""Confirmation tests: Stripe webhook + bKash callback.

Covers the money-critical guarantees: a successful confirmation marks the order
paid and reduces stock exactly once (idempotent on replay), a failed one leaves
the order pending with stock intact, and the provider verification/execution
boundary is monkeypatched so no network is hit.
"""
from app.models.order import OrderStatus
from app.models.payment import Payment, PaymentProvider, PaymentStatus
from app.payments.bkash_provider import BkashProvider
from app.payments.stripe_provider import StripeProvider


async def _seed_pending_payment(
    session, order, provider: PaymentProvider, transaction_id: str
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


def _stripe_event(event_type: str, intent_id: str) -> dict:
    return {"type": event_type, "data": {"object": {"id": intent_id}}}


# --- Stripe webhook ----------------------------------------------------------
async def test_stripe_webhook_success_marks_order_paid(
    client, session, order, product, monkeypatch
):
    payment = await _seed_pending_payment(
        session, order, PaymentProvider.stripe, "pi_x"
    )
    monkeypatch.setattr(
        StripeProvider,
        "_construct_event",
        lambda self, raw_body, signature: _stripe_event(
            "payment_intent.succeeded", "pi_x"
        ),
    )

    resp = await client.post(
        "/api/v1/payments/stripe/webhook",
        content=b"{}",
        headers={"stripe-signature": "whatever"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"received": True}

    await session.refresh(payment)
    await session.refresh(order)
    await session.refresh(product)
    assert payment.status == PaymentStatus.success
    assert order.status == OrderStatus.paid
    assert product.stock == 3  # 5 - 2, reduced on success


async def test_stripe_webhook_replay_is_idempotent(
    client, session, order, product, monkeypatch
):
    await _seed_pending_payment(session, order, PaymentProvider.stripe, "pi_x")
    monkeypatch.setattr(
        StripeProvider,
        "_construct_event",
        lambda self, raw_body, signature: _stripe_event(
            "payment_intent.succeeded", "pi_x"
        ),
    )

    for _ in range(2):
        resp = await client.post(
            "/api/v1/payments/stripe/webhook",
            content=b"{}",
            headers={"stripe-signature": "whatever"},
        )
        assert resp.status_code == 200

    await session.refresh(order)
    await session.refresh(product)
    assert order.status == OrderStatus.paid
    assert product.stock == 3  # decremented exactly once despite the replay


async def test_stripe_webhook_failed_keeps_order_pending(
    client, session, order, product, monkeypatch
):
    payment = await _seed_pending_payment(
        session, order, PaymentProvider.stripe, "pi_y"
    )
    monkeypatch.setattr(
        StripeProvider,
        "_construct_event",
        lambda self, raw_body, signature: _stripe_event(
            "payment_intent.payment_failed", "pi_y"
        ),
    )

    resp = await client.post(
        "/api/v1/payments/stripe/webhook",
        content=b"{}",
        headers={"stripe-signature": "whatever"},
    )
    assert resp.status_code == 200

    await session.refresh(payment)
    await session.refresh(order)
    await session.refresh(product)
    assert payment.status == PaymentStatus.failed
    assert order.status == OrderStatus.pending
    assert product.stock == 5  # untouched


# --- bKash callback ----------------------------------------------------------
async def test_bkash_callback_completed_marks_order_paid(
    client, session, order, product, monkeypatch
):
    payment = await _seed_pending_payment(
        session, order, PaymentProvider.bkash, "bkash_pay_1"
    )

    async def fake_grant(self, http_client):
        return "token"

    async def fake_execute(self, http_client, token, payment_id):
        assert payment_id == "bkash_pay_1"
        return {
            "paymentID": "bkash_pay_1",
            "trxID": "TRX1",
            "transactionStatus": "Completed",
            "statusCode": "0000",
        }

    monkeypatch.setattr(BkashProvider, "_grant_token", fake_grant)
    monkeypatch.setattr(BkashProvider, "_execute", fake_execute)

    resp = await client.post(
        "/api/v1/payments/bkash/callback",
        json={"paymentID": "bkash_pay_1", "status": "success"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "success"
    assert body["transaction_id"] == "bkash_pay_1"

    await session.refresh(payment)
    await session.refresh(order)
    await session.refresh(product)
    assert payment.status == PaymentStatus.success
    assert order.status == OrderStatus.paid
    assert product.stock == 3
