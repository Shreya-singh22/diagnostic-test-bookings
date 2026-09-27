"""Domain errors raised by services and mapped to a consistent JSON error body:

    {"error": {"code": "BOOKING_NOT_FOUND", "message": "...", "details": ...}}
"""

import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    status_code = status.HTTP_400_BAD_REQUEST
    code = "BAD_REQUEST"

    def __init__(self, message: str, *, code: str | None = None, details: Any = None):
        super().__init__(message)
        self.message = message
        self.details = details
        if code:
            self.code = code


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "NOT_FOUND"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    code = "CONFLICT"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "FORBIDDEN"


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "UNAUTHORIZED"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    code = "VALIDATION_ERROR"


class RateLimitedError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    code = "RATE_LIMITED"


def _error_body(code: str, message: str, details: Any = None) -> dict:
    body: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        body["details"] = details
    return {"error": body}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if isinstance(exc, UnauthorizedError) else None
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(_error_body(exc.code, exc.message, exc.details)),
            headers=headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content=_error_body("VALIDATION_ERROR", "Request validation failed", details),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(code.get(exc.status_code, "HTTP_ERROR"), str(exc.detail)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error")
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=_error_body("INTERNAL_ERROR", "An unexpected error occurred"),
        )
