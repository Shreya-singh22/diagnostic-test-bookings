"""Fixed-window rate limiting per client IP, backed by the shared cache."""

import time
from collections.abc import Callable

from fastapi import Request, Response

from app.core.cache import get_cache
from app.core.config import get_settings
from app.core.exceptions import RateLimitedError


def rate_limit(scope: str, limit_setting: str) -> Callable[[Request, Response], None]:
    """Build a dependency allowing `settings.<limit_setting>` requests per window."""

    def dependency(request: Request, response: Response) -> None:
        settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        limit: int = getattr(settings, limit_setting)
        window = settings.rate_limit_window_seconds
        client = request.client.host if request.client else "unknown"
        bucket = int(time.time() // window)
        count = get_cache().incr(f"rl:{scope}:{client}:{bucket}", window)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(max(limit - count, 0))
        if count > limit:
            raise RateLimitedError(
                f"Too many requests; limit is {limit} per {window} seconds",
                details={"retry_after_seconds": window - int(time.time()) % window},
            )

    return dependency
