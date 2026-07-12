"""Authentication API tests: register, login, and the Bearer-token dependency.

These run through `anon_client` (no `get_current_user` override) so the real
JWT path in `app/api/deps.py` is exercised; `GET /api/v1/users/me` serves as
the simplest protected probe endpoint.
"""
from datetime import datetime, timedelta, timezone

import pytest
from jose import jwt as jose_jwt
from sqlmodel import select

from app.auth.jwt_handler import create_access_token, verify_token
from app.auth.password_utils import verify_password
from app.core.config import settings
from app.models.user import User
from tests.conftest import TEST_USER_PASSWORD


def _raw_token(claims: dict) -> str:
    """Encode a JWT directly — `create_access_token` forces `type: "access"`,
    so wrong-type / missing-sub tokens have to be built by hand."""
    payload = {
        "exp": datetime.now(timezone.utc) + timedelta(minutes=5),
        "type": "access",
        **claims,
    }
    return jose_jwt.encode(
        payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


# --- register ------------------------------------------------------------------
async def test_register_success(anon_client, session):
    resp = await anon_client.post(
        "/api/v1/auth/register",
        json={
            "email": "new@example.com",
            "password": "password123",
            "full_name": "New User",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["email"] == "new@example.com"
    assert body["full_name"] == "New User"
    assert body["is_admin"] is False
    assert body["is_active"] is True
    assert "password" not in body
    assert "hashed_password" not in body

    row = (
        await session.execute(select(User).where(User.email == "new@example.com"))
    ).scalar_one()
    assert row.hashed_password != "password123"
    assert verify_password("password123", row.hashed_password)


async def test_register_duplicate_email_409(anon_client, user):
    resp = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": user.email, "password": "password123"},
    )
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"] == "A user with this email already exists."


async def test_register_short_password_422(anon_client):
    resp = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "short@example.com", "password": "seven77"},
    )
    assert resp.status_code == 422, resp.text


async def test_register_invalid_email_422(anon_client):
    resp = await anon_client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "password123"},
    )
    assert resp.status_code == 422, resp.text


# --- login ---------------------------------------------------------------------
async def test_login_success_returns_token(anon_client, pw_user):
    resp = await anon_client.post(
        "/api/v1/auth/login",
        json={"email": pw_user.email, "password": TEST_USER_PASSWORD},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert verify_token(body["access_token"])["sub"] == str(pw_user.id)


async def test_login_wrong_password_401(anon_client, pw_user):
    resp = await anon_client.post(
        "/api/v1/auth/login",
        json={"email": pw_user.email, "password": "wrong-password"},
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Incorrect email or password"
    assert resp.headers["WWW-Authenticate"] == "Bearer"


async def test_login_unknown_email_401(anon_client):
    resp = await anon_client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "password123"},
    )
    assert resp.status_code == 401, resp.text
    # Same message as a bad password — no user-enumeration leak.
    assert resp.json()["detail"] == "Incorrect email or password"


async def test_login_inactive_user_401(anon_client, session, pw_user):
    pw_user.is_active = False
    session.add(pw_user)
    await session.commit()

    resp = await anon_client.post(
        "/api/v1/auth/login",
        json={"email": pw_user.email, "password": TEST_USER_PASSWORD},
    )
    assert resp.status_code == 401, resp.text


async def test_register_login_me_roundtrip(anon_client):
    """The full token path with an API-issued token: register -> login -> /me."""
    creds = {"email": "round@example.com", "password": "password123"}

    resp = await anon_client.post(
        "/api/v1/auth/register", json={**creds, "full_name": "Round Trip"}
    )
    assert resp.status_code == 201, resp.text

    resp = await anon_client.post("/api/v1/auth/login", json=creds)
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]

    resp = await anon_client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == creds["email"]


# --- token dependency (get_current_user) ----------------------------------------
async def test_me_with_factory_token_200(anon_client, pw_user, auth_headers):
    resp = await anon_client.get("/api/v1/users/me", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == pw_user.email


async def test_me_without_token_401(anon_client):
    resp = await anon_client.get("/api/v1/users/me")
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Not authenticated"


async def test_me_wrong_scheme_401(anon_client):
    resp = await anon_client.get(
        "/api/v1/users/me", headers={"Authorization": "Basic dXNlcjpwYXNz"}
    )
    assert resp.status_code == 401, resp.text


_INVALID_TOKENS = {
    "expired": lambda: create_access_token(
        {"sub": "1"}, expires_delta=timedelta(minutes=-5)
    ),
    "wrong-type": lambda: _raw_token({"sub": "1", "type": "refresh"}),
    "missing-sub": lambda: _raw_token({}),
    "non-int-sub": lambda: _raw_token({"sub": "abc"}),
    "unknown-user": lambda: create_access_token({"sub": "999999"}),
    "garbage": lambda: "not.a.jwt",
}


@pytest.mark.parametrize("case", _INVALID_TOKENS)
async def test_me_rejects_invalid_tokens_401(anon_client, case):
    token = _INVALID_TOKENS[case]()
    resp = await anon_client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"] == "Could not validate credentials"


async def test_me_deactivated_user_401(anon_client, session, pw_user, auth_headers):
    """Deactivating a user revokes existing tokens — the row is reloaded on
    every request, so a still-valid JWT no longer authenticates."""
    pw_user.is_active = False
    session.add(pw_user)
    await session.commit()

    resp = await anon_client.get("/api/v1/users/me", headers=auth_headers)
    assert resp.status_code == 401, resp.text
