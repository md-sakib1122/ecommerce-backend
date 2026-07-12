"""Shared pytest fixtures: an in-memory SQLite DB + an ASGI test client.

Tests run entirely on SQLite (no Postgres, no Redis, no network) — the JSON and
Decimal columns are portable, and every external provider call is monkeypatched
in the individual tests. One `AsyncSession` is shared between the fixtures and the
request handlers (injected via a `get_session` override) so a test can assert on
rows a request just committed. `get_current_user` is overridden to a seeded user.
"""
import os
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

# Settings() is constructed at import time and requires these fields, so give
# them safe dummies before any `app.*` import when no developer `.env` exists.
# `setdefault` never overwrites a real environment (e.g. CI); none of the values
# is ever dialed — the DB session is overridden to in-memory SQLite and every
# provider call is monkeypatched.
for _key, _value in {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test",
    "JWT_SECRET_KEY": "test-jwt-secret",
    "STRIPE_SECRET_KEY": "sk_test_dummy",
    "STRIPE_WEBHOOK_SECRET": "whsec_dummy",
    "BKASH_APP_KEY": "dummy-app-key",
    "BKASH_APP_SECRET": "dummy-app-secret",
    "BKASH_USERNAME": "dummy-username",
    "BKASH_PASSWORD": "dummy-password",
}.items():
    os.environ.setdefault(_key, _value)

import app.db.base  # noqa: F401 — registers every model on SQLModel.metadata
from app.api.deps import get_current_user
from app.auth.jwt_handler import create_access_token
from app.auth.password_utils import hash_password
from app.db.session import get_session
from app.main import app as fastapi_app
from app.models.order import Order, OrderStatus
from app.models.order_item import OrderItem
from app.models.product import Product
from app.models.user import User


@pytest.fixture
async def engine():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection => one in-memory DB
    )
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(engine) -> AsyncSession:
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session


@pytest.fixture
async def user(session) -> User:
    user = User(email="buyer@example.com", hashed_password="x", full_name="Buyer")
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
async def product(session) -> Product:
    product = Product(name="Widget", sku="WIDGET-1", price=Decimal("10.00"), stock=5)
    session.add(product)
    await session.commit()
    await session.refresh(product)
    return product


@pytest.fixture
async def order(session, user, product) -> Order:
    """A `pending` order for `user`: 2 x `product` @ 10.00 = 20.00."""
    order = Order(
        user_id=user.id, status=OrderStatus.pending, total_amount=Decimal("20.00")
    )
    order.items = [
        OrderItem(
            product_id=product.id,
            quantity=2,
            price=Decimal("10.00"),
            subtotal=Decimal("20.00"),
        )
    ]
    session.add(order)
    await session.commit()
    await session.refresh(order)
    return order


@pytest.fixture
async def client(session, user) -> AsyncClient:
    """ASGI client with the DB session + current user overridden to the fixtures."""

    async def _get_session():
        yield session

    fastapi_app.dependency_overrides[get_session] = _get_session
    fastapi_app.dependency_overrides[get_current_user] = lambda: user

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    fastapi_app.dependency_overrides.clear()


# --- real-auth fixtures --------------------------------------------------------
TEST_USER_PASSWORD = "correct-horse-battery"
# bcrypt costs ~300 ms per hash — hash once at import instead of per test.
_TEST_PASSWORD_HASH = hash_password(TEST_USER_PASSWORD)


@pytest.fixture
async def anon_client(session) -> AsyncClient:
    """ASGI client with only the DB session overridden — the real Bearer-JWT
    auth path runs, unlike `client` which bypasses `get_current_user`."""

    async def _get_session():
        yield session

    fastapi_app.dependency_overrides[get_session] = _get_session

    transport = ASGITransport(app=fastapi_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client

    fastapi_app.dependency_overrides.pop(get_session, None)


@pytest.fixture
async def pw_user(session) -> User:
    """A user whose password is TEST_USER_PASSWORD — the `user` fixture's
    hashed_password="x" can never verify, so real login tests need this one."""
    user = User(
        email="login@example.com",
        hashed_password=_TEST_PASSWORD_HASH,
        full_name="Login User",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


@pytest.fixture
def auth_headers(pw_user) -> dict[str, str]:
    """Authorization header carrying a real JWT for `pw_user`."""
    token = create_access_token({"sub": str(pw_user.id)})
    return {"Authorization": f"Bearer {token}"}
