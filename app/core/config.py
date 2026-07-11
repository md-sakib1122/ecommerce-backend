"""Environment variables and settings (Pydantic BaseSettings)."""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # --- App ---
    ENV: str = "development"           # development | staging | production
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"
    PROJECT_NAME: str = "Ecommerce Backend"

    # --- Database (Postgres, direct — db/session.py uses this) ---
    DATABASE_URL: str                  # e.g. postgresql+asyncpg://user:pass@localhost:5432/ecommerce
    FRONTEND_URL: str = "http://localhost:3000"
    # --- Auth / JWT (core/security.py) ---
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24        # 24h
    REFRESH_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7   # 7d

    # --- Redis (cache/redis_client.py — category tree cache) ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CATEGORY_CACHE_TTL_SECONDS: int = 60 * 60          # 1h

    # --- Stripe (payments/stripe_provider.py) ---
    STRIPE_SECRET_KEY: str
    STRIPE_WEBHOOK_SECRET: str          # verifies incoming webhook signatures
    STRIPE_PUBLISHABLE_KEY: str | None = None   # only needed if exposed to frontend via an endpoint

    # --- bKash (payments/bkash_provider.py) ---
    BKASH_APP_KEY: str
    BKASH_APP_SECRET: str
    BKASH_USERNAME: str
    BKASH_PASSWORD: str
    BKASH_BASE_URL: str = "https://tokenized.sandbox.bka.sh/v1.2.0-beta"  # sandbox by default
    # Where bKash redirects the payer after checkout; our callback executes the payment.
    BKASH_CALLBACK_URL: str = "https://proven-vocally-skinhead.ngrok-free.dev/api/v1/payments/bkash/callback"

    # --- Order / business rules ---
    DEFAULT_CURRENCY: str = "BDT"
    LOW_STOCK_THRESHOLD: int = 5

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    @field_validator("ENV")
    @classmethod
    def validate_env(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"ENV must be one of {allowed}")
        return v

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_db_url(cls, v: str) -> str:
        if not v.startswith("postgresql+asyncpg://"):
            raise ValueError(
                "DATABASE_URL must use the asyncpg driver, "
                "e.g. postgresql+asyncpg://user:pass@host:5432/dbname"
            )
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """Cached so Settings() is only constructed once per process."""
    return Settings()


settings = get_settings()