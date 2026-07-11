# SQLModel metadata import point (for alembic autogen)
"""
Central import point for Alembic autogenerate.

SQLModel.metadata only knows about a table once its class has been
imported somewhere in the process. Alembic's env.py imports THIS file
(not the individual model files), so every model must be imported here
or its table will silently be skipped during `alembic revision --autogenerate`.

The running FastAPI app also relies on this module: app/db/session.py imports
it so that every model class is registered on the SQLAlchemy mapper registry
before the first ORM query. The models use string forward-refs in their
relationships (e.g. Order.items -> "OrderItem"), which only resolve if all
referenced classes have been imported. Without this, the first ORM request
would fail with an "failed to locate a name" mapper error.
"""

from sqlmodel import SQLModel

# Import every table model here, even if it looks unused — the import
# itself is the side effect that registers the table into SQLModel.metadata.

from app.models.category import Category  # noqa: F401
from app.models.order import Order  # noqa: F401
from app.models.order_item import OrderItem  # noqa: F401
from app.models.payment import Payment  # noqa: F401
from app.models.product import Product  # noqa: F401
from app.models.user import User  # noqa: F401

# Exposed for alembic/env.py:  target_metadata = SQLModel.metadata
__all__ = ["SQLModel"]