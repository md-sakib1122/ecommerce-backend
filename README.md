# E-Commerce Ordering & Payment System

Backend system for managing users, products, orders, and payments with support for
multiple payment providers (Stripe, bKash). Built for the Backend Engineer take-home
assessment.

## Tech Stack

- **Framework:** FastAPI
- **ORM:** SQLModel (SQLAlchemy + Pydantic)
- **Database:** PostgreSQL
- **Migrations:** Alembic
- **Cache:** Redis (category tree caching)
- **Payments:** Stripe (test mode), bKash (sandbox)
- **Testing:** Pytest

## Folder Structure

```
ecommerce-backend/
├── app/
│   ├── main.py                    # FastAPI app init, router mounting, CORS
│   │
│   ├── core/
│   │   ├── config.py              # Settings (pydantic-settings): DB url, Stripe/bKash keys, Redis url
│   │   ├── security.py            # JWT create/verify, password hashing
│   │   └── logging.py             # Logger config
│   │
│   ├── db/
│   │   ├── session.py             # Engine + get_session() dependency
│   │   └── base.py                # SQLModel metadata import point (for alembic autogen)
│   │
│   ├── models/                    # SQLModel table classes (1 file = 1 table, keeps diffs clean)
│   │   ├── user.py
│   │   ├── category.py
│   │   ├── product.py
│   │   ├── order.py
│   │   ├── order_item.py
│   │   └── payment.py
│   │
│   ├── schemas/                   # Pydantic request/response models (separate from DB models)
│   │   ├── user.py
│   │   ├── product.py
│   │   ├── order.py
│   │   └── payment.py
│   │
│   ├── api/
│   │   ├── deps.py                # get_current_user, get_current_admin, common deps
│   │   └── v1/
│   │       ├── router.py          # include_router() aggregator
│   │       ├── auth.py            # /register /login
│   │       ├── users.py
│   │       ├── categories.py
│   │       ├── products.py
│   │       ├── orders.py
│   │       └── payments.py        # checkout + webhook endpoints
│   │
│   ├── services/                  # OOP business logic classes — routes stay thin
│   │   ├── user_service.py        # class UserService
│   │   ├── product_service.py     # class ProductService -> reduce_stock() (atomic/deterministic)
│   │   ├── order_service.py       # class OrderService -> calculate_totals()
│   │   ├── category_service.py    # class CategoryService -> DFS traversal + Redis cache
│   │   └── payment_service.py     # class PaymentService -> uses strategy from payments/
│   │
│   ├── payments/                  # Strategy pattern lives here, isolated from order logic
│   │   ├── base.py                # abstract PaymentProvider (create, confirm, query)
│   │   ├── stripe_provider.py     # class StripeProvider(PaymentProvider)
│   │   ├── bkash_provider.py      # class BkashProvider(PaymentProvider)
│   │   └── factory.py             # get_provider(name) -> PaymentProvider
│   │
│   ├── cache/
│   │   └── redis_client.py        # Redis connection + get/set category tree
│   │
│   └── utils/
│       └── exceptions.py          # Custom exception classes + handlers
│
├── alembic/
│   ├── versions/
│   └── env.py
├── alembic.ini
│
├── tests/
│   ├── conftest.py                # test DB fixture, test client
│   ├── test_users.py
│   ├── test_products.py
│   ├── test_orders.py
│   ├── test_payments.py
│   └── test_webhooks.py
│
├── seeders/
│   └── seed.py                    # admin user + sample products/categories
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml         # api + postgres + redis
│
├── docs/
│   ├── ERD.png
│   ├── architecture.png
│   └── payment_flows.md
│
├── .env.example
├── requirements.txt
└── README.md
```

## Design Highlights

| Requirement (from brief) | Where it lives |
|---|---|
| OOP classes for User/Product/Order/Payment | `app/services/*_service.py` |
| Strategy pattern for payment providers | `app/payments/` (`base.py` interface, `stripe_provider.py` / `bkash_provider.py` implementations, `factory.py` selector) |
| Deterministic total/subtotal calculation | `app/services/order_service.py` |
| Safe stock reduction after payment | `app/services/product_service.py` |
| DFS category tree traversal | `app/services/category_service.py` |
| Redis caching of category tree | `app/cache/redis_client.py` |
| DB migrations | `alembic/` |
| Models vs API contracts kept separate | `app/models/` (DB tables) vs `app/schemas/` (request/response shapes) |

## Getting Started

### 1. Clone and configure environment

```bash
cp .env.example .env
# fill in DATABASE_URL, REDIS_URL, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
# BKASH_APP_KEY, BKASH_APP_SECRET, BKASH_USERNAME, BKASH_PASSWORD
```

### 2. Run with Docker (Postgres + Redis + API)

```bash
docker compose -f docker/docker-compose.yml up --build
```

### 3. Run migrations

```bash
alembic upgrade head
```

### 4. Seed sample data (admin user + sample products/categories)

```bash
python seeders/seed.py
```

### 5. Run tests

```bash
pytest
```

### 6. API docs

Once running, interactive API docs are available at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Payment Providers

- **Stripe:** integrated in test mode using test API keys (`pk_test_...` / `sk_test_...`)
  and Stripe test card numbers. Switching to live mode only requires swapping API keys
  and verifying the webhook signing secret — no code changes needed.
- **bKash:** integrated against the Tokenized Checkout sandbox
  (`https://tokenized.sandbox.bka.sh/v1.2.0-beta`).

See `docs/payment_flows.md` for detailed sequence diagrams of both flows.

## Documentation

- **ERD:** `docs/ERD.png`
- **Architecture diagram:** `docs/architecture.png`
- **Payment flow diagrams:** `docs/payment_flows.md`
- **API documentation:** Swagger UI (see above) / Postman collection