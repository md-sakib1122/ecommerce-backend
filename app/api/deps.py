"""Shared FastAPI dependencies for authentication and authorization.

Auth model: Bearer JWT + `User.is_admin` (no string roles). The token's `sub`
claim holds the user id; we load the row fresh on every request so deactivated
or deleted users lose access immediately.
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.jwt_handler import verify_token
from app.core.config import settings
from app.db.session import get_session
from app.models.user import User
from app.services.user_service import UserService

# tokenUrl is documentation metadata for Swagger; the scheme just extracts the
# `Authorization: Bearer <token>` header. Login itself accepts a JSON body.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")

_credentials_exc = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    payload = verify_token(token)
    if payload is None:
        raise _credentials_exc

    subject = payload.get("sub")
    if subject is None:
        raise _credentials_exc
    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise _credentials_exc

    user = await UserService(session).get_by_id(user_id)
    if user is None or not user.is_active:
        raise _credentials_exc
    return user


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return current_user
