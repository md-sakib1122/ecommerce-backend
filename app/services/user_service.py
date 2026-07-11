"""OOP business logic for users.

`UserService` owns everything the user-management feature needs: registration,
authentication, profile updates, and the read queries that back a user's own
orders/payments views plus the admin user-management endpoints.
"""
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import select

from app.auth.password_utils import hash_password, verify_password
from app.models.order import Order
from app.models.payment import Payment
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate


class UserService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- lookups -------------------------------------------------------------
    async def get_by_id(self, user_id: int) -> User | None:
        return await self.session.get(User, user_id)

    async def get_by_email(self, email: str) -> User | None:
        result = await self.session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    # --- registration / auth -------------------------------------------------
    async def register(self, data: UserCreate) -> User:
        """Create a new general user. Email must be unique (409 otherwise)."""
        if await self.get_by_email(data.email) is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists.",
            )

        user = User(
            email=data.email,
            hashed_password=hash_password(data.password),
            full_name=data.full_name,
        )
        self.session.add(user)
        try:
            await self.session.commit()
        except IntegrityError:
            # Lost the race against a concurrent insert on the unique email.
            await self.session.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A user with this email already exists.",
            )
        await self.session.refresh(user)
        return user

    async def authenticate(self, email: str, password: str) -> User | None:
        """Return the user iff the credentials are valid and the account is active."""
        user = await self.get_by_email(email)
        if user is None or not verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None
        return user

    # --- profile -------------------------------------------------------------
    async def update_profile(self, user: User, data: UserUpdate) -> User:
        """Self-service update. Only `full_name` may be changed by the owner;
        `is_active` is reserved for admins (see `set_active`)."""
        if data.full_name is not None:
            user.full_name = data.full_name
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    # --- own orders / payments ----------------------------------------------
    async def list_orders(self, user_id: int) -> list[Order]:
        result = await self.session.execute(
            select(Order)
            .where(Order.user_id == user_id)
            .options(selectinload(Order.items))
            .order_by(Order.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_payments(self, user_id: int) -> list[Payment]:
        # Payment has no user_id; ownership is enforced by joining through Order.
        result = await self.session.execute(
            select(Payment)
            .join(Order, Payment.order_id == Order.id)
            .where(Order.user_id == user_id)
            .order_by(Payment.created_at.desc())
        )
        return list(result.scalars().all())

    # --- admin ---------------------------------------------------------------
    async def list_users(self, skip: int = 0, limit: int = 100) -> list[User]:
        result = await self.session.execute(
            select(User).order_by(User.id).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def get_user_or_404(self, user_id: int) -> User:
        user = await self.get_by_id(user_id)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User {user_id} not found.",
            )
        return user

    async def set_active(self, user_id: int, is_active: bool) -> User:
        user = await self.get_user_or_404(user_id)
        user.is_active = is_active
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user
