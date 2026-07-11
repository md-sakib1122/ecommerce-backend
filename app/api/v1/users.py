"""User routes: self-service profile + own orders/payments, plus admin management.

Route ordering matters: the literal `/me*` paths are declared before the
`/{user_id}` path so "me" is never captured as an id.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin, get_current_user
from app.db.session import get_session
from app.models.user import User
from app.schemas.order import OrderRead
from app.schemas.payment import PaymentRead
from app.schemas.user import UserRead, UserUpdate
from app.services.user_service import UserService

router = APIRouter(prefix="/users", tags=["users"])


# --- self-service (any authenticated user) ----------------------------------
@router.get("/me", response_model=UserRead)
async def read_own_profile(
    current_user: User = Depends(get_current_user),
) -> UserRead:
    return UserRead.model_validate(current_user)


@router.patch("/me", response_model=UserRead)
async def update_own_profile(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    user = await UserService(session).update_profile(current_user, data)
    return UserRead.model_validate(user)


@router.get("/me/orders", response_model=list[OrderRead])
async def read_own_orders(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[OrderRead]:
    orders = await UserService(session).list_orders(current_user.id)
    return [OrderRead.model_validate(o) for o in orders]


@router.get("/me/payments", response_model=list[PaymentRead])
async def read_own_payments(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PaymentRead]:
    payments = await UserService(session).list_payments(current_user.id)
    return [PaymentRead.model_validate(p) for p in payments]


# --- admin-only user management ---------------------------------------------
@router.get("", response_model=list[UserRead])
async def list_users(
    skip: int = 0,
    limit: int = 100,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> list[UserRead]:
    users = await UserService(session).list_users(skip=skip, limit=limit)
    return [UserRead.model_validate(u) for u in users]


@router.get("/{user_id}", response_model=UserRead)
async def get_user(
    user_id: int,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    user = await UserService(session).get_user_or_404(user_id)
    return UserRead.model_validate(user)


@router.patch("/{user_id}", response_model=UserRead)
async def set_user_active(
    user_id: int,
    data: UserUpdate,
    _admin: User = Depends(get_current_admin),
    session: AsyncSession = Depends(get_session),
) -> UserRead:
    """Admin activate/deactivate a user. `is_active` is taken from the body;
    `full_name` is ignored here (that's the user's own via PATCH /users/me)."""
    is_active = data.is_active if data.is_active is not None else True
    user = await UserService(session).set_active(user_id, is_active)
    return UserRead.model_validate(user)
