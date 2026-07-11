# JWT create/verify — reads the canonical settings from app.core.config.
from datetime import datetime, timedelta, timezone

from jose import ExpiredSignatureError, JWTError, jwt

from app.core.config import settings


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Create a signed JWT access token.

    The payload is stamped with an expiry (`exp`) and a `type` claim so that
    `verify_token` can reject non-access tokens (e.g. future refresh tokens).
    """
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode.update({"exp": expire, "type": "access"})
    return jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def verify_token(token: str) -> dict | None:
    """Decode and validate a JWT access token.

    Returns the decoded payload on success, or ``None`` for any failure
    (expired, malformed, bad signature, or wrong token type). Callers treat
    ``None`` as "not authenticated".
    """
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
        )
    except (ExpiredSignatureError, JWTError):
        return None

    if payload.get("type") != "access":
        return None
    return payload
