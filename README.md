# E-Commerce Ordering & Payment System

A backend system for managing users, products, orders, and payments with support for
multiple payment providers (Stripe, bKash). Built for the Backend Engineer take-home
assessment.

## Table of Contents

- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Design Highlights](#design-highlights)
- [Getting Started](#getting-started)
  - [Option A: Docker Setup](#option-a-docker-setup-recommended)
  - [Option B: Local Setup (without Docker)](#option-b-local-setup-without-docker)
- [Database Migrations (Alembic)](#database-migrations-alembic)
- [Running the Test Suite](#running-the-test-suite)
- [Payment Providers](#payment-providers)
- [API Documentation](#api-documentation)
- [Documentation](#documentation)
- [Important Notes](#important-notes)

## Tech Stack

- **Framework:** FastAPI
- **ORM:** SQLModel (SQLAlchemy + Pydantic)
- **Database:** PostgreSQL
- **Migrations:** Alembic
- **Cache:** Redis (category tree caching)
- **Payments:** Stripe (test mode), bKash (sandbox)
- **Testing:** Pytest

## Project Structure

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
│   ├── test_auth.py
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
│   ├── system_architecture.png
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

There are two ways to run the project: with **Docker** (brings up the API, Postgres, and
Redis together) or **locally** with a Python virtual environment (requires Postgres and
Redis to already be running/reachable).

> All commands below are run from the **repository root** (`ecommerce-backend/`, the
> folder that contains `pytest.ini`, `requirements.txt`, and `app/`).

### Option A: Docker Setup (recommended)

The Docker setup lives in `docker/` and brings up **three services**:

| Service | Image | Port | Notes |
| --- | --- | --- | --- |
| `api` | Built from `docker/Dockerfile` | `8000` | FastAPI app run with Uvicorn |
| `db` | `postgres:16-alpine` | `5432` | Persists to the `postgres_data` volume; has a healthcheck |
| `redis` | `redis:7-alpine` | `6379` | Cache only (ephemeral); has a healthcheck |

The `api` service waits for `db` and `redis` to be **healthy** before it starts
(`depends_on: condition: service_healthy`).

The image (`docker/Dockerfile`) is a single-stage build on **`python:3.12-slim`**, runs
as a **non-root** user (`appuser`), and starts:
`uvicorn app.main:app --host 0.0.0.0 --port 8000`.

> **Build context is the repo root**, not the `docker/` folder — the compose file
> declares `context: ..` with `dockerfile: docker/Dockerfile`. Always run commands from
> the repo root and pass `-f docker/docker-compose.yml`.

The `api` service loads secrets from `../.env` (`env_file`), but compose **overrides**
`DATABASE_URL` and `REDIS_URL` so that inside the container they point at the service
hostnames `db` and `redis` (not `127.0.0.1`):

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/ecommerce
REDIS_URL=redis://redis:6379/0
```

The `127.0.0.1` values in `.env` only matter when running the app **outside** Docker
(see Option B). Environment variables required and with **no default** (the app fails to
start without them):

- `DATABASE_URL`, `JWT_SECRET_KEY`
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
- `BKASH_APP_KEY`, `BKASH_APP_SECRET`, `BKASH_USERNAME`, `BKASH_PASSWORD`

Use `.env.example` as the template.

#### Steps

```bash
# 1. Create the env file that compose loads (../.env), then fill in secrets
cp .env.example .env

# 2. Build the images and start api + postgres + redis
docker compose -f docker/docker-compose.yml up --build
#    add -d to run in the background (detached)

# 3. Apply the database migrations — this creates the tables (NOT run automatically)
docker compose -f docker/docker-compose.yml exec api alembic upgrade head

# 4. (Optional) seed initial data
docker compose -f docker/docker-compose.yml exec api python seeders/seed.py
```

Once it's up:

| URL | What |
| --- | --- |
| `http://localhost:8000` | API root |
| `http://localhost:8000/health` | Health check → `{"status": "ok", ...}` |
| `http://localhost:8000/docs` | Swagger UI |
| `http://localhost:8000/redoc` | ReDoc |

#### Useful Docker commands

```bash
# Follow the API logs
docker compose -f docker/docker-compose.yml logs -f api

# Stop the containers (keeps the postgres_data volume / your data)
docker compose -f docker/docker-compose.yml down

# Stop AND wipe the database volume (fresh start)
docker compose -f docker/docker-compose.yml down -v

# Rebuild only the api image after changing code or dependencies
docker compose -f docker/docker-compose.yml build api
```

**Dev hot-reload:** in `docker/docker-compose.yml`, uncomment the `volumes:` and
`command:` blocks under the `api` service. That bind-mounts `../app` into the container
and adds `--reload`, so code changes take effect without rebuilding.

> Older Docker installs use the hyphenated `docker-compose` command instead of
> `docker compose`. Everything else (including the `-f docker/docker-compose.yml` flag) is
> identical.

### Option B: Local Setup (without Docker)

Requires PostgreSQL and Redis already running and reachable (see `.env.example` for
connection settings).

```bash
# 1. From the project root
cd ecommerce-backend

# 2. Create a virtual environment
python -m venv venv
```

Activate it:

```powershell
# Windows (PowerShell)
venv\Scripts\activate
```

```bash
# Linux / macOS
source venv/bin/activate
```

Then set up the interpreter in your IDE to point at `venv`, and continue:

```bash
# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment variables
cp .env.example .env
#    fill in DATABASE_URL, REDIS_URL, STRIPE_SECRET_KEY, STRIPE_WEBHOOK_SECRET,
#    BKASH_APP_KEY, BKASH_APP_SECRET, BKASH_USERNAME, BKASH_PASSWORD

# 5. Apply migrations
alembic upgrade head

# 6. (Optional) seed sample data (admin user + sample products/categories)
python seeders/seed.py

# 7. Run the dev server
uvicorn app.main:app --reload
```

The API will be available at `http://localhost:8000`, with interactive docs at `/docs`
and `/redoc`.

## Database Migrations (Alembic)

Alembic manages PostgreSQL schema changes based on the SQLModel table definitions in
`app/models/`.

### Apply migrations

```bash
alembic upgrade head
```

Migrates the database to the most up-to-date Alembic revision — runs every migration
file that hasn't been applied yet, in order, up to `head`.

### Generate a new migration after changing a model

```bash
alembic revision --autogenerate -m "add updated model changes"
```

Compares the current PostgreSQL database schema against the SQLModel/SQLAlchemy models
and automatically writes any structural differences it finds (new tables, columns,
indexes, etc.) into a brand-new migration file under `alembic/versions/`. Always review
the generated file before committing — autogenerate doesn't reliably catch everything
(e.g., some column type changes or renames).

## Running the Test Suite

### What the tests are built on

| Thing | Detail |
| --- | --- |
| Test runner | **pytest** `8.3.4` |
| Async support | **pytest-asyncio** `0.25.0`, `asyncio_mode = auto` |
| Config file | `pytest.ini` (`testpaths = tests`) |
| Test database | **In-memory SQLite** (`aiosqlite`) with a `StaticPool` |
| HTTP client | `httpx.AsyncClient` over `ASGITransport` (no live server started) |
| App under test | `app.main:app`, routes mounted under `/api/v1` |

Because `asyncio_mode = auto`, every `async def test_*` runs as an async test
automatically — no need to add `@pytest.mark.asyncio`.

### No setup needed — no `.env`, no Postgres, no real keys

The suite is fully self-contained:

- Tests run on an **in-memory SQLite** database created fresh for each test, so you do
  **not** need Postgres or Redis running.
- `tests/conftest.py` sets safe dummy environment variables (via `os.environ.setdefault`)
  **before** importing the app, so you do **not** need a `.env` file or real
  Stripe / bKash / JWT secrets.
- All external provider calls (Stripe, bKash) are **monkeypatched**, so no network access
  and no live API keys are required.

> ⚠ **Caveat:** if a real `.env` file exists in the project root, Pydantic loads it and
> those values override the test dummies. The tests still pass (providers are mocked
> either way), but be aware the app reads your `.env` at import time.

### Key fixtures (defined in `tests/conftest.py`)

| Fixture | What it gives you |
| --- | --- |
| `engine` | Fresh async in-memory SQLite engine (created + disposed per test) |
| `session` | The shared `AsyncSession` — the *same* one the request handlers use, so you can assert on rows a request just committed |
| `user` | A seeded buyer `User` |
| `product` | A seeded `Product` (`Widget`, price `10.00`, stock `5`) |
| `order` | A seeded pending `Order` for `user` (2 × product = `20.00`) |
| `client` | `AsyncClient` with **auth bypassed** (`get_session` + `get_current_user` overridden) — use for normal API tests |
| `anon_client` | `AsyncClient` with only `get_session` overridden — the **real JWT/Bearer path** runs (used for auth + auth-guard tests) |
| `pw_user` | A user whose password is known (`TEST_USER_PASSWORD`), for real login tests |
| `auth_headers` | `{"Authorization": "Bearer <jwt>"}` carrying a real token for `pw_user` |

### What each test file covers

| File | Covers |
| --- | --- |
| `tests/test_auth.py` | Register / login endpoints and the Bearer-token dependency, including a parametrized matrix of invalid tokens (expired, wrong-type, garbage, …) |
| `tests/test_orders.py` | Order create / read / cancel, ownership isolation (foreign orders → `404`, not `403`), and money/stock rules (server-side totals, price snapshotting, no stock reserved at creation) |
| `tests/test_payments.py` | The payment strategy factory (`Stripe` / `bKash`) and the checkout endpoint, with provider I/O monkeypatched |
| `tests/test_webhooks.py` | Stripe webhook + bKash callback confirmation, verifying stock is decremented **exactly once** and replays are idempotent |

### Commands

```bash
# Install dependencies (once), from the repo root
pip install -r requirements.txt

# Run the whole suite (auto-discovers tests/ via testpaths)
pytest

# Verbose output
pytest -v

# Run a single file
pytest tests/test_auth.py

# Run a single test
pytest tests/test_orders.py::test_create_order_success

# Run one case from a parametrized test
pytest "tests/test_auth.py::test_me_rejects_invalid_tokens_401[expired]"

# If `pytest` isn't on your PATH, run it via the module
python -m pytest
```

### Coverage (optional)

`pytest-cov` is **not** included in `requirements.txt`, so install it separately:

```bash
pip install pytest-cov

# Terminal report showing which lines are missing coverage
pytest --cov=app --cov-report=term-missing

# HTML report — then open htmlcov/index.html in a browser
pytest --cov=app --cov-report=html
```

## Payment Providers

- **Stripe:** integrated in test mode using test API keys (`pk_test_...` / `sk_test_...`)
  and Stripe test card numbers. Switching to live mode only requires swapping API keys
  and verifying the webhook signing secret — no code changes needed.
- **bKash:** integrated against the Tokenized Checkout sandbox
  (`https://tokenized.sandbox.bka.sh/v1.2.0-beta`).

See `docs/payment_flows.md` for detailed sequence diagrams of both flows.

## API Documentation

Once the app is running, interactive API docs are available at:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

## Documentation

- **ERD:** `docs/ERD.png`
- **Architecture diagram:** `docs/architecture.png`
- **System architecture diagram:** `docs/system_architecture.png`
- **Payment flow diagrams:** `docs/payment_flows.md`
- **API documentation:** Swagger UI / ReDoc (see above) / Postman collection

## Important Notes

1. **Scrub the sample credentials.** `.env.example` ships with what look like **real
   bKash sandbox credentials** (`BKASH_USERNAME`, `BKASH_PASSWORD`). Rotate or redact
   these before publishing or sharing the repo.
2. **Migrations do not run automatically.** The container's startup only checks database
   connectivity (`SELECT 1`); it does **not** create tables. You must run
   `alembic upgrade head` or the first request will fail with missing tables.
3. **The default `CMD` is single-worker Uvicorn** with no `--reload`. For real
   production, add workers / a process manager and set `ENV=production` (which also
   disables `/docs` and `/redoc`).
4. **A bare `.env` boots but can't take payments.** Copying `.env.example` gives you
   empty Stripe/bKash keys — the app starts fine, but payment flows won't work until you
   fill in real keys.