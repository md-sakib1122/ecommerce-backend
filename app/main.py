# FastAPI app init, router mounting, CORS
"""FastAPI app instance, router includes, CORS, startup events."""
"""
IELTS Writing Practice App — FastAPI entry point.

Responsibilities:
  - Create and configure the FastAPI application instance
  - Register all v1 routers under /api/v1
  - Apply CORS middleware
  - Wire up lifestartup / shutdown)
  - Expose a health-check endppan events (soint
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.api.v1.router import api_router

# ---------------------------------------------------------------------------
# Lifespan — startup / shutdown
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Runs once on startup (before first request) and once on shutdown.

    Startup:
      - Validate critical env vars (settings raises on missing values)
      - Initialise the Supabase service-role client
      - (Optional) warm up LiteLLM model list / connection pool

    Shutdown:
      - Cleanly close the Supabase client / any open HTTP sessions
    """
    # --- startup ---

    # 2. Store it directly on the app instance!
    app.state.supabase = supabase_client
    print(f"[startup] Environment : {settings.ENV}")
    print(f"[startup] LLM provider: {settings.DEFAULT_LLM_MODEL}")
    print("[startup] Application ready ✓")

    yield  # <-- application runs here

    # --- shutdown ---
    print("[shutdown] Closing Supabase client …")
    await close_supabase_client()
    print("[shutdown] Goodbye.")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="IELTS Writing Practice API",
        description=(
            "Backend for the IELTS Writing Practice App. "
            "Provides writing sessions, AI-powered feedback (grammar, ideas, "
            "vocabulary), band scoring, and subscription management."
        ),
        version="0.1.0",
        docs_url="/docs" if settings.ENV != "production" else None,
        redoc_url="/redoc" if settings.ENV != "production" else None,
        lifespan=lifespan,
    )

    # -----------------------------------------------------------------------
    # CORS
    # -----------------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,   # e.g. ["http://localhost:5173"]
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -----------------------------------------------------------------------
    # Routers
    # -----------------------------------------------------------------------
    app.include_router(api_router, prefix="/api/v1")

    # -----------------------------------------------------------------------
    # Health check  (unauthenticated, used by load-balancers / uptime monitors)
    # -----------------------------------------------------------------------
    @app.get("/health", tags=["health"], include_in_schema=False)
    async def health_check() -> JSONResponse:
        return JSONResponse({"status": "ok", "env": settings.ENV})

    return app


# ---------------------------------------------------------------------------
# Module-level app instance  (picked up by Uvicorn / Gunicorn)
# ---------------------------------------------------------------------------

app: FastAPI = create_app()