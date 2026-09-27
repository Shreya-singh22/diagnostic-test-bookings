from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, loaded from environment variables (and `.env`)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "EVE Diagnostics Booking API"
    database_url: str = "postgresql+psycopg://eve:eve@localhost:5432/eve"
    # Empty string disables Redis; caching and rate limiting fall back gracefully.
    redis_url: str = ""

    jwt_secret: str = "dev-only-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    bcrypt_rounds: int = 12

    # Shared secret used by the (simulated) payment provider to sign webhooks.
    webhook_secret: str = "dev-only-webhook-secret"

    # Probability that a simulated payment succeeds when the client does not
    # force an outcome.
    payment_success_rate: float = 0.8

    cache_ttl_seconds: int = 60

    rate_limit_enabled: bool = True
    auth_rate_limit: int = 10  # requests per window per client IP
    payment_rate_limit: int = 20
    rate_limit_window_seconds: int = 60

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
