"""
E-Commerce Ordering & Payment System — FastAPI entry point.

Responsibilities:
  - Create and configure the FastAPI application instance
  - Register all v1 routers under /api/v1
  - Apply CORS middleware
  - Wire up lifespan (startup / shutdown) — verifies DB connectivity
  - Expose a health-check endpoint
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.db.session import engine, get_session
from app.api.v1.router import api_router


# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Runs once on startup (before first request) and once on shutdown.

    Startup:
      - Confirm the DB engine can actually open a connection
        (fails fast if Postgres/Redis env vars are wrong, instead of
        the first API request mysteriously erroring)

    Shutdown:
      - Dispose the engine's connection pool cleanly
    """
    # --- startup ---
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1")) # cheap round-trip to confirm DB is reachable
    print(f"[startup] Environment : {settings.ENV}")
    print("[startup] Database connection OK ✓")
    print("[startup] Application ready ✓")

    yield  # <-- application runs here

    # --- shutdown ---
    print("[shutdown] Disposing DB engine …")
    await engine.dispose()
    print("[shutdown] Goodbye.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    app = FastAPI(
        title="E-Commerce Ordering & Payment API",
        description=(
            "Backend for managing users, products, orders, and payments "
            "with support for multiple payment providers (Stripe, bKash)."
        ),
        version="0.1.0",
        docs_url="/docs" if settings.ENV != "production" else None,
        redoc_url="/redoc" if settings.ENV != "production" else None,
        lifespan=lifespan,
    )

    # -------------------------------------------------------------------
    # CORS
    # -------------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,  # e.g. ["http://localhost:5173"]
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -------------------------------------------------------------------
    # Routers
    # -------------------------------------------------------------------
    app.include_router(api_router, prefix="/api/v1")

    # -------------------------------------------------------------------
    # Health check (unauthenticated, used by load balancers / uptime monitors)
    # Uses get_session via Depends — the SAME dependency your routes use —
    # to prove the DB is reachable through the normal request path.
    # -------------------------------------------------------------------
    @app.get("/health", tags=["health"], include_in_schema=False)
    async def health_check(
            session: AsyncSession = Depends(get_session),
    ) -> JSONResponse:
        await session.execute(text("SELECT 1"))
        return JSONResponse({"status": "ok", "env": settings.ENV})

    return app


# ---------------------------------------------------------------------------
# Module-level app instance (picked up by Uvicorn / Gunicorn)
# ---------------------------------------------------------------------------
app: FastAPI = create_app()