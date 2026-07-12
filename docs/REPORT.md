# Project Report — E-Commerce Backend

This document answers the four assessment questions: how I built the project and why, what alternatives I rejected, how I tested it (with real results), and my final verdict on the outcome.

---

## 1. Implementation Approach and Rationale

### Technology stack

| Concern | Choice | Why |
|---|---|---|
| Web framework | **FastAPI** (+ Uvicorn) | Native async support, automatic request validation via Pydantic, and free interactive OpenAPI docs (`/docs`, `/redoc`) |
| Database | **PostgreSQL** (async via `asyncpg`) | Orders, order items, stock, and payments are relational and transactional — ACID guarantees and foreign-key integrity are non-negotiable for money |
| ORM | **SQLModel** (SQLAlchemy 2.0 + Pydantic) | One class per table doubles as a validated data model; avoids duplicating every entity as both an ORM model and a Pydantic schema |
| Migrations | **Alembic** | Schema changes are versioned and reproducible (`alembic upgrade head`) |
| Auth | **JWT** (python-jose) + **bcrypt** (passlib) | Stateless bearer tokens; passwords never stored in recoverable form |
| Payments | **Stripe SDK** + **bKash** via raw `httpx` REST calls | Stripe for international cards, bKash for the local (Bangladesh) market; bKash has no official Python SDK, so its tokenized-checkout API is called directly |
| Cache | **Redis** (`redis.asyncio`) | Caches the rendered category tree, which is read-heavy and expensive to recompute (recursive traversal) |

### Architecture: layered, thin routers → OOP services

The codebase is organized so that each layer has exactly one job:

```
app/
├── api/v1/        # Routers: parse/validate request, delegate, return schema
├── services/      # OOP business-logic classes (OrderService, PaymentService, ...)
├── payments/      # Payment-provider Strategy classes (pure external I/O)
├── models/        # SQLModel table definitions (one file per table)
├── schemas/       # Request/response Pydantic models (kept separate from DB models)
├── auth/          # JWT creation/verification + password hashing
├── db/            # Async engine, session factory, get_session dependency
├── cache/         # Redis client
└── core/          # Settings (pydantic-settings) + logging
```

- **Routers are deliberately thin.** An endpoint validates input against a schema and calls one service method (e.g. `PaymentService(session).initiate_checkout(...)`). All business rules live in `app/services/`, which keeps them unit-testable and keeps HTTP concerns out of the domain logic.
- **Dependency injection via FastAPI `Depends`.** `get_session`, `get_current_user`, and `get_current_admin` (`app/api/deps.py`) are injected into endpoints. This is what makes the test suite possible — tests swap the database and auth with overrides instead of patching internals.
- **Strategy pattern for payment providers** (`app/payments/`). An abstract `PaymentStrategy` (`base.py`) defines the provider contract; `StripeProvider` and `BkashProvider` implement it; `factory.get_payment_strategy()` selects one by name. Strategies do *pure provider I/O only* — they never touch the database. All persistence, state transitions, and idempotency live in `PaymentService`, so adding a new provider (e.g. Nagad, SSLCommerz) means one new subclass and one factory registry entry, with zero changes to order/payment business logic.

### Correctness and security decisions

These are the decisions I consider the core of the design, because an e-commerce backend is primarily a money-correctness problem:

- **Server-side money math.** Order totals are computed on the server with `Decimal` (quantized to cents), never trusted from the client. Unit prices are **snapshotted** into `order_items.price` at order creation, so later product price changes cannot alter historical orders.
- **Oversell protection.** Stock is decremented with an atomic `UPDATE products SET stock = stock - :qty WHERE id = :id AND stock >= :qty` and a rowcount check (`ProductService.reduce_stock`), so two concurrent payments can never both take the last unit.
- **Idempotent payment confirmation.** `payments.transaction_id` is a UNIQUE column and acts as the idempotency key. A replayed Stripe webhook or bKash callback for an already-successful payment is a no-op, and `OrderService.mark_paid()` only transitions `pending → paid` once — so stock is decremented exactly once no matter how many times a webhook is delivered.
- **Webhook trust model.** The Stripe webhook verifies the `Stripe-Signature` header against the raw request body (`stripe.Webhook.construct_event`) and rejects bad signatures with 400. The bKash callback is unauthenticated by design, so the server **never trusts its parameters** — it re-calls bKash's `execute` API server-side and uses that authoritative response to decide success or failure.
- **Authorization details.** `get_current_user` re-loads the user row on every request and checks `is_active`, so deactivating a user revokes access immediately even with a valid token. Ownership checks on orders/payments return **404 instead of 403** so an attacker cannot probe which resource IDs exist.
- **Soft-delete for products.** Deleting a product sets `status = inactive` rather than removing the row, preserving the integrity of historical order items that reference it.
- **Hierarchical categories.** Categories form a self-referential tree (adjacency list with `parent_id`), traversed with DFS for the tree endpoint and for "all products in this branch"; admin operations include cycle guards (a category cannot be re-parented under itself or a descendant).

Further detail, including sequence diagrams for both payment flows, is in `docs/payment_flows.md`, and the ERD/architecture diagrams are in `docs/`.

---

## 2. Rejected Alternatives

### Django + Django REST Framework (framework alternative)

Django/DRF was the obvious "batteries included" option — it ships an admin panel, an ORM, and a mature auth system. I rejected it because:

- This project is a **pure JSON API** with two external HTTP integrations (Stripe, bKash). FastAPI's native `async`/`await` lets the bKash `httpx` calls and all database I/O run without blocking a worker; Django's ORM async story is still partial, and DRF is effectively synchronous.
- DRF requires serializers defined separately from models; FastAPI + SQLModel gives me validation, serialization, and OpenAPI documentation from the same type annotations, with much less boilerplate for a project of this size.
- The Django admin — its biggest selling point — was not a requirement here.

### MongoDB (database alternative)

A document store looks attractive for a product catalog (flexible attributes per product). I rejected it because the *hard* part of this domain is not the catalog, it's the money:

- An order confirmation must atomically flip the order to `paid`, record the payment, and decrement stock across multiple product rows. That is a **multi-row, multi-table ACID transaction** — exactly what PostgreSQL does natively and what MongoDB only approximates (multi-document transactions exist but are bolted on and come with significant constraints).
- Orders → order items → products → users are inherently **relational**; foreign keys and unique constraints (e.g. the UNIQUE `transaction_id` that underpins payment idempotency) are enforced by the database itself rather than by application discipline.
- The one "hierarchical" piece of the schema — the category tree — is handled fine in SQL with an adjacency list.

### `if/else` provider branching instead of the Strategy pattern (design alternative)

The quickest way to support two payment providers is `if provider == "stripe": ... elif provider == "bkash": ...` inside the payment service. I rejected it because:

- Stripe and bKash have **structurally different flows** (Stripe: create PaymentIntent → signed webhook; bKash: grant token → create → user redirect → server-side execute). Branching on provider at every step would scatter provider-specific code through checkout, confirmation, and callback handling.
- With the Strategy interface, provider results are normalized into shared dataclasses (`CheckoutResult`, `PaymentOutcome`, `ConfirmationContext`), so `PaymentService` contains **zero provider conditionals** and each provider is independently testable by mocking a single I/O boundary.
- The cost of the pattern (three small extra files) is trivial compared to the extensibility gained.

### Raw SQLAlchemy + separate Pydantic schemas (ORM alternative)

Classic SQLAlchemy declarative models plus hand-written Pydantic schemas is the most battle-tested stack. I chose SQLModel instead to avoid defining every entity twice; SQLModel still *is* SQLAlchemy 2.0 underneath, so I lose nothing at the query level, and I still keep separate response schemas (`app/schemas/`) where the API shape must differ from the table shape.

---

## 3. Testing Approach and Reports

### Strategy

The suite is **integration-style API tests through the real ASGI app**, with only the true externals replaced:

- **pytest + pytest-asyncio** (`asyncio_mode = auto`), driven by `httpx.AsyncClient` against the FastAPI app — every test exercises routing, validation, the service layer, and the ORM together, not isolated functions.
- **In-memory SQLite** (`sqlite+aiosqlite://` with `StaticPool`) replaces PostgreSQL via a `get_session` dependency override. Tests need no running database, no Redis, and no network, so the whole suite runs in ~5 seconds and is fully deterministic. (The `Payment.raw_response` column deliberately uses generic `JSON` instead of Postgres-only `JSONB` to keep this possible.)
- **Two client fixtures** (`tests/conftest.py`): `client` overrides authentication to focus on business logic, while `anon_client` overrides *only* the database — so the auth tests exercise the **real** JWT signing/verification and bcrypt hashing paths end-to-end.
- **Provider I/O is monkeypatched at the narrowest boundary** — the private methods that actually hit the network (`StripeProvider._create_intent`, `BkashProvider._grant_token`, `_create`, `_execute`) — so everything above that seam (strategy logic, factory, `PaymentService`, webhook handlers) runs for real.

### What is covered (63 tests)

| File | Tests | Focus |
|---|---|---|
| `tests/test_auth.py` | 19 | Register (success / duplicate 409 / weak password 422 / bad email 422), login (success / wrong password / unknown email / inactive user — all 401 with no user-enumeration leak), full register→login→`/me` roundtrip, a parametrized matrix of 6 invalid-token cases (expired, wrong type, missing/invalid subject, unknown user, garbage), and immediate revocation for deactivated users |
| `tests/test_orders.py` | 16 | Order creation (validation, inactive product, insufficient stock, duplicate-item aggregation), **price snapshotting**, owner-only reads (foreign order → 404), cancel rules (pending-only, idempotent, paid → 409), auth guard |
| `tests/test_payments.py` | 15 | Strategy factory selection, checkout for both providers creating pending payments, non-pending order → 409, duplicate `transaction_id` → 409, malformed provider response → 502, owner-only payment reads |
| `tests/test_webhooks.py` | 13 | Stripe webhook success/failure paths, **bad signature → 400**, **replayed webhooks are idempotent** (stock decremented exactly once), bKash callback for Completed/Initiated/Failed statuses, unknown transaction → 404, success on a canceled order → 409 |

### Results

Full run on 2026-07-12 (`python -m pytest -v`), **63 passed, 0 failed** in 4.93s:

```text
============================= test session starts =============================
platform win32 -- Python 3.11.3, pytest-8.3.4, pluggy-1.6.0
rootdir: C:\...\ecommerce-backend
configfile: pytest.ini
plugins: anyio-4.14.1, asyncio-0.25.0
asyncio: mode=Mode.AUTO
collected 63 items

tests/test_auth.py::test_register_success PASSED                         [  1%]
tests/test_auth.py::test_register_duplicate_email_409 PASSED             [  3%]
tests/test_auth.py::test_register_short_password_422 PASSED              [  4%]
tests/test_auth.py::test_register_invalid_email_422 PASSED               [  6%]
tests/test_auth.py::test_login_success_returns_token PASSED              [  7%]
tests/test_auth.py::test_login_wrong_password_401 PASSED                 [  9%]
tests/test_auth.py::test_login_unknown_email_401 PASSED                  [ 11%]
tests/test_auth.py::test_login_inactive_user_401 PASSED                  [ 12%]
tests/test_auth.py::test_register_login_me_roundtrip PASSED              [ 14%]
tests/test_auth.py::test_me_with_factory_token_200 PASSED                [ 15%]
tests/test_auth.py::test_me_without_token_401 PASSED                     [ 17%]
tests/test_auth.py::test_me_wrong_scheme_401 PASSED                      [ 19%]
tests/test_auth.py::test_me_rejects_invalid_tokens_401[expired] PASSED   [ 20%]
tests/test_auth.py::test_me_rejects_invalid_tokens_401[wrong-type] PASSED [ 22%]
tests/test_auth.py::test_me_rejects_invalid_tokens_401[missing-sub] PASSED [ 23%]
tests/test_auth.py::test_me_rejects_invalid_tokens_401[non-int-sub] PASSED [ 25%]
tests/test_auth.py::test_me_rejects_invalid_tokens_401[unknown-user] PASSED [ 26%]
tests/test_auth.py::test_me_rejects_invalid_tokens_401[garbage] PASSED   [ 28%]
tests/test_auth.py::test_me_deactivated_user_401 PASSED                  [ 30%]
tests/test_orders.py::test_create_order_success PASSED                   [ 31%]
tests/test_orders.py::test_create_order_aggregates_duplicate_products PASSED [ 33%]
tests/test_orders.py::test_create_order_missing_product_404 PASSED       [ 34%]
tests/test_orders.py::test_create_order_inactive_product_400 PASSED      [ 36%]
tests/test_orders.py::test_create_order_insufficient_stock_400 PASSED    [ 38%]
tests/test_orders.py::test_create_order_empty_items_422 PASSED           [ 39%]
tests/test_orders.py::test_create_order_zero_quantity_422 PASSED         [ 41%]
tests/test_orders.py::test_order_price_is_snapshotted PASSED             [ 42%]
tests/test_orders.py::test_get_order_owner_200 PASSED                    [ 44%]
tests/test_orders.py::test_get_order_missing_404 PASSED                  [ 46%]
tests/test_orders.py::test_get_foreign_order_404 PASSED                  [ 47%]
tests/test_orders.py::test_cancel_pending_order_200 PASSED               [ 49%]
tests/test_orders.py::test_cancel_is_idempotent PASSED                   [ 50%]
tests/test_orders.py::test_cancel_paid_order_409 PASSED                  [ 52%]
tests/test_orders.py::test_cancel_foreign_order_404 PASSED               [ 53%]
tests/test_orders.py::test_orders_require_auth_401 PASSED                [ 55%]
tests/test_payments.py::test_factory_returns_correct_strategy PASSED     [ 57%]
tests/test_payments.py::test_factory_unsupported_provider_raises_400 PASSED [ 58%]
tests/test_payments.py::test_checkout_stripe_creates_pending_payment PASSED [ 60%]
tests/test_payments.py::test_checkout_bkash_creates_pending_payment PASSED [ 61%]
tests/test_payments.py::test_checkout_non_pending_order_conflicts PASSED [ 63%]
tests/test_payments.py::test_checkout_foreign_order_not_found PASSED     [ 65%]
tests/test_payments.py::test_checkout_body_url_mismatch_400 PASSED       [ 66%]
tests/test_payments.py::test_checkout_missing_order_404 PASSED           [ 68%]
tests/test_payments.py::test_checkout_invalid_provider_422 PASSED        [ 69%]
tests/test_payments.py::test_checkout_duplicate_transaction_id_409 PASSED [ 71%]
tests/test_payments.py::test_checkout_bkash_malformed_create_502 PASSED  [ 73%]
tests/test_payments.py::test_get_payment_owner_200 PASSED                [ 74%]
tests/test_payments.py::test_get_payment_foreign_404 PASSED              [ 76%]
tests/test_payments.py::test_get_payment_missing_404 PASSED              [ 77%]
tests/test_payments.py::test_payments_require_auth_401 PASSED            [ 79%]
tests/test_webhooks.py::test_stripe_webhook_success_marks_order_paid PASSED [ 80%]
tests/test_webhooks.py::test_stripe_webhook_replay_is_idempotent PASSED  [ 82%]
tests/test_webhooks.py::test_stripe_webhook_failed_keeps_order_pending PASSED [ 84%]
tests/test_webhooks.py::test_bkash_callback_completed_marks_order_paid PASSED [ 85%]
tests/test_webhooks.py::test_bkash_callback_initiated_stays_pending PASSED [ 87%]
tests/test_webhooks.py::test_bkash_callback_failed_keeps_order_pending PASSED [ 88%]
tests/test_webhooks.py::test_bkash_callback_missing_params_422 PASSED    [ 90%]
tests/test_webhooks.py::test_bkash_callback_unknown_payment_404 PASSED   [ 92%]
tests/test_webhooks.py::test_bkash_callback_replay_is_idempotent PASSED  [ 93%]
tests/test_webhooks.py::test_stripe_webhook_bad_signature_400 PASSED     [ 95%]
tests/test_webhooks.py::test_stripe_webhook_unknown_transaction_404 PASSED [ 96%]
tests/test_webhooks.py::test_stripe_webhook_unhandled_event_stays_pending PASSED [ 98%]
tests/test_webhooks.py::test_stripe_webhook_success_on_canceled_order_409 PASSED [100%]

======================= 63 passed, 6 warnings in 4.93s ========================
```

The 6 warnings are Pydantic v2 deprecation notices about class-based `Config` in response schemas (cosmetic; migrating to `ConfigDict` is a known cleanup item).

To reproduce: `pip install -r requirements.txt && python -m pytest -v` — no database, Redis, or API keys required.

### Manual testing

Beyond the automated suite, I tested the full flows manually:

- **Swagger UI** (`/docs`) against a local PostgreSQL instance (migrated with `alembic upgrade head` and seeded via `seeders/seed.py`) for the register → login → browse → order → checkout journey.
- **bKash sandbox end-to-end** with the real bKash tokenized-checkout sandbox, exposing the local server through an ngrok tunnel so bKash could reach the callback URL. This surfaced a real integration issue — bKash returns the user via a GET callback rather than a POST webhook — which I fixed by reworking the callback endpoint (commit `8dc3831`, "bkash web hook url issue fix").
- **Stripe test mode** with test keys and the Stripe CLI–style webhook secret for signature verification.

---

## 4. Final Verdict

### What went well

- **The architecture held up.** The thin-router / service-layer / strategy split meant every feature landed in a predictable place, and the payment abstraction proved itself when the bKash flow turned out to be structurally different from Stripe's — the differences stayed contained inside `BkashProvider`.
- **Money-correctness was designed in, not patched in.** Idempotent webhook handling, atomic stock decrements, price snapshotting, and server-side `Decimal` totals were built as first-class requirements, and each one is pinned by an automated test.
- **The test suite is fast and honest.** 63 tests in ~5 seconds with no external dependencies, exercising the real app stack including the real JWT/bcrypt code paths — fast enough to run on every change.
- **Real-world integration.** The system was verified against the actual bKash sandbox, not just mocks, which caught a genuine callback-semantics issue that mocks alone would have missed.

### Limitations that remain

- **Docker deployment is incomplete.** `docker/docker-compose.yml` is a stub; the documented `docker compose up` path does not work yet, so the project currently requires a locally installed PostgreSQL/Redis.
- **No refresh-token flow.** Only 24-hour access tokens are issued; the refresh-token expiry is configured but the endpoint was never implemented, so users must re-login when the token expires.
- **Stock is not reserved at order creation.** Availability is checked when the order is placed but only decremented on payment, so an item can sell out between checkout start and payment completion (the atomic decrement then fails the late payer safely, but the UX is imperfect).
- **Test coverage gaps.** The product and user/admin endpoints currently lack dedicated automated tests (their earlier test files were removed during a test reorganization and not yet rewritten), and there is no load/concurrency testing.
- **Operational hardening.** No rate limiting, no CI pipeline, and the category cache is the only caching in place.

### What I would improve next time

1. Finish the Docker Compose setup (API + PostgreSQL + Redis) so the project runs with one command, and add a GitHub Actions CI job running the test suite.
2. Implement the refresh-token endpoint and short-lived access tokens.
3. Add time-boxed stock reservation at order creation (reserve on order, release on cancel/expiry) to close the sell-out window.
4. Restore and extend product/user endpoint tests, and add a concurrency test proving the oversell guard under parallel payments.
5. Migrate response schemas to Pydantic `ConfigDict` to clear the deprecation warnings, and add rate limiting on auth and checkout endpoints.

Overall, I consider the project a success: the core assessment requirements — a layered OOP backend with JWT auth, hierarchical categories, order management, and two correctly-integrated payment providers — are implemented, documented, and verified by a passing automated test suite, with the remaining gaps being deployment and hardening work rather than core-domain defects.
