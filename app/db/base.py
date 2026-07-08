# SQLModel metadata import point (for alembic autogen)
"""
Central import point for Alembic autogenerate.

SQLModel.metadata only knows about a table once its class has been
imported somewhere in the process. Alembic's env.py imports THIS file
(not the individual model files), so every model must be imported here
or its table will silently be skipped during `alembic revision --autogenerate`.

This file is NOT used by the running FastAPI app — routes/services import
models directly from app.models.*. This file exists purely for Alembic.
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