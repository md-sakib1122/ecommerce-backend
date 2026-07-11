# Payment Flow Diagrams (Stripe & bKash)

How the Payment System (§2.1.4–2.1.6) initiates and settles payments across
providers using the **Strategy pattern**, so a new provider can be added without
touching order logic.

## Layering

```
HTTP  →  app/api/v1/payments.py     thin routes: parse schema, call PaymentService
         app/services/payment_service.py   persistence, idempotency, calls mark_paid
         app/payments/factory.py     get_payment_strategy(provider) -> PaymentStrategy
         app/payments/base.py        PaymentStrategy (abstract) + normalized dataclasses
         app/payments/{stripe,bkash}_provider.py   concrete strategies (pure provider I/O)
```

- `PaymentService` and `OrderService` depend **only** on the abstract
  `PaymentStrategy`; the concrete provider is chosen by `get_payment_strategy`.
  Adding a provider = new `PaymentStrategy` subclass + one entry in
  `factory._REGISTRY`. No order-flow changes.
- Strategies never touch the database. All persistence, the `Payment` row, and
  the `OrderService.mark_paid()` call live in `PaymentService`.
- Data crossing the seam is normalized (`app/payments/base.py`):
  `CheckoutResult`, `PaymentOutcome`, `ConfirmationContext`.

## Idempotency

`payments.transaction_id` is **UNIQUE** and is the idempotency key (Stripe →
`payment_intent_id`, bKash → `paymentID`). Both are known at checkout, so the
pending `Payment` row is created then and confirmations look it up by
`transaction_id` (never by `order_id` — an order can have several attempts).

A confirmation for an already-`success` payment is a no-op, and
`OrderService.mark_paid()` is independently idempotent (`pending → paid` once),
so **a replayed webhook never double-decrements stock**. Stock is reduced only
inside `mark_paid()`, after a successful payment — never at order creation.

## Stripe flow (PaymentIntent + webhook)

```
Client                      API (this service)                 Stripe
  |  POST /orders/{id}/checkout {provider:"stripe"}  |
  |------------------------------------------------->|
  |                          create PaymentIntent --------------->|
  |                          intent (id, client_secret) <---------|
  |         persist Payment(pending, txn=pi_...)      |
  |  200 {client_secret, payment_intent_id}          |
  |<-------------------------------------------------|
  |  confirm card-side with client_secret ----------------------->|
  |                                                   |  webhook  |
  |                          POST /payments/stripe/webhook <------|
  |                          verify signature (STRIPE_WEBHOOK_SECRET)
  |                          payment_intent.succeeded -> success  |
  |                          mark_paid(order) => paid + reduce stock
  |                          200 {received:true} --------------->|
```

- `StripeProvider.create_payment` → `PaymentIntent.create` (async client).
  `amount` is the order total in the smallest currency unit (`total * 100`).
- `StripeProvider.confirm` → `stripe.Webhook.construct_event(raw_body,
  Stripe-Signature, STRIPE_WEBHOOK_SECRET)`; a bad signature is a **400**.
  `payment_intent.succeeded → success`, `payment_intent.payment_failed → failed`,
  anything else → `pending`.
- Stored: `provider="stripe"`, `transaction_id=payment_intent_id`,
  `status=pending|success|failed`, `raw_response=<event/intent>`.

## bKash flow (tokenized checkout + execute)

```
Client                      API (this service)                 bKash
  |  POST /orders/{id}/checkout {provider:"bkash"}   |
  |------------------------------------------------->|
  |                          grant token ----------------------->|
  |                          create payment --------------------->|
  |                          {paymentID, bkashURL} <-------------|
  |         persist Payment(pending, txn=paymentID)  |
  |  200 {bkash_url, payment_id}                     |
  |<-------------------------------------------------|
  |  redirect to bkash_url, user pays --------------------------->|
  |  bKash redirects to callbackURL (BKASH_CALLBACK_URL)         |
  |                          POST /payments/bkash/callback {paymentID}
  |                          grant token; EXECUTE payment ------->|
  |                          {transactionStatus:"Completed"} <----|
  |                          -> success; mark_paid => paid + reduce stock
  |                          200 PaymentRead                     |
```

- bKash has no official SDK — `BkashProvider` calls the sandbox REST API with
  `httpx.AsyncClient` (`grant → create → execute`).
- The callback is **unauthenticated**, so we never trust its body: confirmation
  always calls **execute** server-side and maps bKash's `transactionStatus`
  (`Completed → success`, `Initiated → pending`, else `failed`). A forged
  callback can't mark an order paid because bKash's own execute is the source of
  truth.
- Stored: `provider="bkash"`, `transaction_id=paymentID`,
  `status=pending|success|failed`, `raw_response=<execute response>`.

## Order status transitions

`pending → paid` on the first successful confirmation (via `mark_paid`);
`pending → canceled` only via `POST /orders/{id}/cancel` (before payment). A
failed payment leaves the order `pending`, so the user can retry with a new
attempt (a new `Payment` row, new `transaction_id`).

## Config

`STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`; `BKASH_APP_KEY`,
`BKASH_APP_SECRET`, `BKASH_USERNAME`, `BKASH_PASSWORD`, `BKASH_BASE_URL`,
`BKASH_CALLBACK_URL` — all in `app/core/config.py` (`.env`).
