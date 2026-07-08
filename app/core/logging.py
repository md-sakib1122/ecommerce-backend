# Logger config
# Settings (pydantic-settings): DB url, Stripe/bKash keys, Redis url
"""Environment variables and settings (Pydantic BaseSettings)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator


class Settings(BaseSettings):
    # --- App ---
    ENV: str = "development"        # development | staging | production
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"

    # --- Database ---
    DATABASE_URL: str               # e.g. postgresql+psycopg2://user:pass@localhost:5432/ecommerce

    # --- Redis (category tree caching) ---
    REDIS_URL: str = "redis://localhost:6379/0"
    CATEGORY_TREE_CACHE_TTL_SECONDS: int = 3600

    # --- Auth / JWT (used in core/security.py) ---
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    # --- Stripe ---
    STRIPE_SECRET_KEY: str
    STRIPE_PUBLISHABLE_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str

    # --- bKash (Tokenized Checkout) ---
    BKASH_BASE_URL: str = "https://tokenized.sandbox.bka.sh/v1.2.0-beta"
    BKASH_APP_KEY: str
    BKASH_APP_SECRET: str
    BKASH_USERNAME: str
    BKASH_PASSWORD: str

    # --- CORS ---
    CORS_ORIGINS: list[str] = ["http://localhost:5173"]

    @field_validator("ENV")
    @classmethod
    def validate_env(cls, v: str) -> str:
        allowed = {"development", "staging", "production"}
        if v not in allowed:
            raise ValueError(f"ENV must be one of {allowed}")
        return v

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",   # ignore unrelated env vars instead of erroring
    )


@lru_cache
def get_settings() -> Settings:
    """Cached so Settings() is only constructed once per process."""
    return Settings()


settings = get_settings()