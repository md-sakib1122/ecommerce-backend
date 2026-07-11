from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings

# Register every SQLModel table on the mapper registry. Models wire their
# relationships with string forward-refs (e.g. Order.items -> "OrderItem"),
# which SQLAlchemy resolves at first-query mapper configuration — that requires
# every model class to have been imported. Importing this here (the DB
# chokepoint used by the app, deps, services, and seeders) guarantees the graph
# is complete before any ORM query runs.
import app.db.base  # noqa: F401,E402  (imported for its registration side effects)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session