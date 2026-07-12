"""Confirmation tests: Stripe webhook + bKash callback.

Covers the money-critical guarantees: a successful confirmation marks the order
paid and reduces stock exactly once (idempotent on replay), a failed one leaves
the order pending with stock intact, and the provider verification/execution
boundary is monkeypatched so no network is hit.
"""
from app.core.config import settings
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

    # The callback is a GET redirect target: bKash sends the payer's browser
    # here with query params, and we 303 them on to the frontend result page.
    resp = await client.get(
        "/api/v1/payments/bkash/callback",
        params={"paymentID": "bkash_pay_1", "status": "success"},
    )
    assert resp.status_code == 303, resp.text
    assert resp.headers["location"] == (
        f"{settings.FRONTEND_URL}/orders/result?status=success&paymentID=bkash_pay_1"
    )

    await session.refresh(payment)
    await session.refresh(order)
    await session.refresh(product)
    assert payment.status == PaymentStatus.success
    assert order.status == OrderStatus.paid
    assert product.stock == 3


def _mock_bkash_execute(monkeypatch, payment_id: str, transaction_status: str):
    """Fake the bKash server-side execute to return the given transactionStatus."""

    async def fake_grant(self, http_client):
        return "token"

    async def fake_execute(self, http_client, token, pid):
        return {
            "paymentID": payment_id,
            "transactionStatus": transaction_status,
            "statusCode": "0000",
        }

    monkeypatch.setattr(BkashProvider, "_grant_token", fake_grant)
    monkeypatch.setattr(BkashProvider, "_execute", fake_execute)


async def test_bkash_callback_initiated_stays_pending(
    client, session, order, product, monkeypatch
):
    payment = await _seed_pending_payment(
        session, order, PaymentProvider.bkash, "bkash_pay_2"
    )
    _mock_bkash_execute(monkeypatch, "bkash_pay_2", "Initiated")

    resp = await client.get(
        "/api/v1/payments/bkash/callback",
        params={"paymentID": "bkash_pay_2", "status": "success"},
    )
    assert resp.status_code == 303, resp.text
    assert "status=pending" in resp.headers["location"]

    await session.refresh(payment)
    await session.refresh(order)
    await session.refresh(product)
    assert payment.status == PaymentStatus.pending
    assert order.status == OrderStatus.pending
    assert product.stock == 5


async def test_bkash_callback_failed_keeps_order_pending(
    client, session, order, product, monkeypatch
):
    payment = await _seed_pending_payment(
        session, order, PaymentProvider.bkash, "bkash_pay_3"
    )
    # Anything other than Completed/Initiated maps to failed.
    _mock_bkash_execute(monkeypatch, "bkash_pay_3", "Cancelled")

    resp = await client.get(
        "/api/v1/payments/bkash/callback",
        params={"paymentID": "bkash_pay_3", "status": "failure"},
    )
    assert resp.status_code == 303, resp.text
    assert "status=failed" in resp.headers["location"]

    await session.refresh(payment)
    await session.refresh(order)
    await session.refresh(product)
    assert payment.status == PaymentStatus.failed
    assert order.status == OrderStatus.pending
    assert product.stock == 5


async def test_bkash_callback_missing_params_422(client):
    resp = await client.get("/api/v1/payments/bkash/callback")
    assert resp.status_code == 422, resp.text


async def test_bkash_callback_unknown_payment_404(client, monkeypatch):
    _mock_bkash_execute(monkeypatch, "ghost", "Completed")

    resp = await client.get(
        "/api/v1/payments/bkash/callback",
        params={"paymentID": "ghost", "status": "success"},
    )
    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == "No payment found for transaction ghost."


async def test_bkash_callback_replay_is_idempotent(
    client, session, order, product, monkeypatch
):
    await _seed_pending_payment(session, order, PaymentProvider.bkash, "bkash_pay_4")
    _mock_bkash_execute(monkeypatch, "bkash_pay_4", "Completed")

    for _ in range(2):
        resp = await client.get(
            "/api/v1/payments/bkash/callback",
            params={"paymentID": "bkash_pay_4", "status": "success"},
        )
        assert resp.status_code == 303, resp.text

    await session.refresh(order)
    await session.refresh(product)
    assert order.status == OrderStatus.paid
    assert product.stock == 3  # decremented exactly once despite the replay


# --- Stripe webhook error paths ------------------------------------------------
async def test_stripe_webhook_bad_signature_400(client, monkeypatch):
    def raise_bad_signature(self, raw_body, signature):
        raise ValueError("bad signature")

    monkeypatch.setattr(StripeProvider, "_construct_event", raise_bad_signature)

    resp = await client.post(
        "/api/v1/payments/stripe/webhook",
        content=b"{}",
        headers={"stripe-signature": "bogus"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "Invalid Stripe webhook signature."


async def test_stripe_webhook_unknown_transaction_404(client, monkeypatch):
    monkeypatch.setattr(
        StripeProvider,
        "_construct_event",
        lambda self, raw_body, signature: _stripe_event(
            "payment_intent.succeeded", "pi_ghost"
        ),
    )

    resp = await client.post(
        "/api/v1/payments/stripe/webhook",
        content=b"{}",
        headers={"stripe-signature": "whatever"},
    )
    assert resp.status_code == 404, resp.text


async def test_stripe_webhook_unhandled_event_stays_pending(
    client, session, order, product, monkeypatch
):
    payment = await _seed_pending_payment(
        session, order, PaymentProvider.stripe, "pi_z"
    )
    monkeypatch.setattr(
        StripeProvider,
        "_construct_event",
        lambda self, raw_body, signature: _stripe_event(
            "payment_intent.created", "pi_z"
        ),
    )

    resp = await client.post(
        "/api/v1/payments/stripe/webhook",
        content=b"{}",
        headers={"stripe-signature": "whatever"},
    )
    assert resp.status_code == 200, resp.text

    await session.refresh(payment)
    await session.refresh(product)
    assert payment.status == PaymentStatus.pending
    assert product.stock == 5


async def test_stripe_webhook_success_on_canceled_order_409(
    client, session, order, product, monkeypatch
):
    payment = await _seed_pending_payment(
        session, order, PaymentProvider.stripe, "pi_c"
    )
    order.status = OrderStatus.canceled
    session.add(order)
    await session.commit()

    monkeypatch.setattr(
        StripeProvider,
        "_construct_event",
        lambda self, raw_body, signature: _stripe_event(
            "payment_intent.succeeded", "pi_c"
        ),
    )

    resp = await client.post(
        "/api/v1/payments/stripe/webhook",
        content=b"{}",
        headers={"stripe-signature": "whatever"},
    )
    assert resp.status_code == 409, resp.text

    await session.refresh(payment)
    await session.refresh(product)
    assert payment.status == PaymentStatus.success  # durable even though mark_paid conflicted
    assert product.stock == 5  # a canceled order never decrements stock
