import logging
import time
import uuid

from fastapi import FastAPI, Request
from sqlalchemy import text

from app.api.deps import DbSession
from app.api.routes import auth, bookings, centres, payments
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, request_id_var

logger = logging.getLogger("app.request")

DESCRIPTION = """
Backend for booking diagnostic tests with a simulated payment gateway.

**Auth:** call `POST /auth/login`, then click *Authorize* and paste the `access_token`.

**Errors** always look like `{"error": {"code": "...", "message": "...", "details": ...}}`.
"""


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title=settings.app_name, version="1.0.0", description=DESCRIPTION)
    register_exception_handlers(app)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            return response
        finally:
            request_id_var.reset(token)

    @app.get("/health", tags=["health"])
    def health(db: DbSession) -> dict:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    for module in (auth, centres, bookings, payments):
        app.include_router(module.router)
    return app


app = create_app()
