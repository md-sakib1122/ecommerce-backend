# Running the Project — Tests & Docker

This guide covers two things:

1. **[Running the Test Suite](#1-running-the-test-suite)** — how to run and use the tests in `tests/`.
2. **[Dockerizing the App](#2-dockerizing-the-app)** — how to build and run the whole stack with Docker.

> All commands are run from the **repository root**
> (`ecommerce-backend/`, the folder that contains `pytest.ini`, `requirements.txt`, and `app/`).
> Commands are shown for **Windows PowerShell**; the bash equivalent is noted where it differs.

---

## 1. Running the Test Suite

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
automatically — you do **not** need to add `@pytest.mark.asyncio`.

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

```powershell
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

```powershell
pip install pytest-cov

# Terminal report showing which lines are missing coverage
pytest --cov=app --cov-report=term-missing

# HTML report — then open htmlcov/index.html in a browser
pytest --cov=app --cov-report=html
```

---

## 2. Dockerizing the App

The Docker setup lives in `docker/` and brings up **three services**:

| Service | Image | Port | Notes |
| --- | --- | --- | --- |
| `api` | Built from `docker/Dockerfile` | `8000` | FastAPI app run with Uvicorn |
| `db` | `postgres:16-alpine` | `5432` | Persists to the `postgres_data` volume; has a healthcheck |
| `redis` | `redis:7-alpine` | `6379` | Cache only (ephemeral); has a healthcheck |

The `api` service waits for `db` and `redis` to be **healthy** before it starts
(`depends_on: condition: service_healthy`).

### The image at a glance (`docker/Dockerfile`)

- Base: **`python:3.12-slim`** (single-stage build).
- Runs as a **non-root** user (`appuser`), workdir `/code`.
- Installs `requirements.txt` first (cached layer), then copies `app/`, `alembic/`,
  `alembic.ini`, and `seeders/`.
- Exposes port **`8000`** and starts:
  `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

> **Build context is the repo root**, not the `docker/` folder. The compose file declares
> `context: ..` with `dockerfile: docker/Dockerfile`. **Rule of thumb: run every command
> from the repo root and always pass `-f docker/docker-compose.yml`.**

### Environment variables

The `api` service loads secrets from `../.env` (`env_file`), but compose **overrides**
`DATABASE_URL` and `REDIS_URL` so that inside the container they point at the service
hostnames `db` and `redis` (not `127.0.0.1`):

```
DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/ecommerce
REDIS_URL=redis://redis:6379/0
```

So the `127.0.0.1` values in your `.env` only matter when running the app **outside**
Docker. The variables that are **required and have no default** (the app fails to start
without them) are:

- `DATABASE_URL`, `JWT_SECRET_KEY`
- `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`
- `BKASH_APP_KEY`, `BKASH_APP_SECRET`, `BKASH_USERNAME`, `BKASH_PASSWORD`

Use `.env.example` as the template.

### Steps to build and run

```powershell
# 1. Create the env file that compose loads (../.env), then fill in secrets
Copy-Item .env.example .env          # bash: cp .env.example .env

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

### Useful commands

```powershell
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

> **Note:** older Docker installs use the hyphenated `docker-compose` command instead of
> `docker compose`. Everything else (including the `-f docker/docker-compose.yml` flag) is
> identical.

### ⚠ Important notes

1. **Scrub the sample credentials.** `.env.example` ships with what look like **real bKash
   sandbox credentials** (`BKASH_USERNAME`, `BKASH_PASSWORD`). Rotate or redact these
   before publishing or sharing the repo.
2. **Migrations do not run automatically.** The container's startup only checks database
   connectivity (`SELECT 1`); it does **not** create tables. You must run
   `alembic upgrade head` (step 3 above) or the first request will fail with missing
   tables.
3. **The default `CMD` is single-worker Uvicorn** with no `--reload`. For real production,
   add workers / a process manager and set `ENV=production` (which also disables `/docs`
   and `/redoc`).
4. **A bare `.env` boots but can't take payments.** Copying `.env.example` gives you empty
   Stripe/bKash keys — the app starts fine, but payment flows won't work until you fill in
   real keys.
