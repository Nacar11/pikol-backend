import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger("pikol.error")

SLOT_CONSTRAINT = "uq_court_slot_active"


class ErrorResponse(BaseModel):
    detail: str
    # Machine-readable, for the frontend to branch on — e.g. EMAIL_NOT_VERIFIED
    # renders a resend action (spec §6.6). `detail` is for humans and may change.
    code: str | None = None
    request_id: str


class AppError(Exception):
    """An expected failure with a stable code, rendered through the envelope."""

    def __init__(self, status: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


def constraint_of(exc: IntegrityError) -> str | None:
    """The violated constraint's name, as asyncpg reports it."""
    cause = getattr(exc.orig, "__cause__", None)
    name = getattr(cause, "constraint_name", None)
    return name if isinstance(name, str) else None


def _envelope(request: Request, status: int, detail: str, code: str | None = None) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(detail=detail, code=code, request_id=request_id).model_dump(),
        headers={"X-Request-ID": request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        return _envelope(request, exc.status, exc.detail, exc.code)

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        """ONLY the slot-uniqueness violation is a 409.

        Two players racing for one slot is the expected outcome of §4.1 and
        deserves a 409. Every OTHER integrity error is a bug: a missing NOT
        NULL column, an FK violation, a duplicate email on registration.
        Dressing those as "That slot is no longer available" lies to the
        caller on a request that has nothing to do with slots, tells them to
        retry something that can never succeed, and hides a genuine fault
        from 5xx alerting.
        """
        if constraint_of(exc) == SLOT_CONSTRAINT:
            return _envelope(request, 409, "That slot is no longer available.", "SLOT_UNAVAILABLE")

        logger.exception("unhandled integrity error")
        return _envelope(request, 500, "Internal server error.")

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        """Without this, an unhandled exception escapes to Starlette's
        ServerErrorMiddleware, which emits a bare 500 carrying NO
        X-Request-ID — so the one class of response where a support engineer
        most needs the correlation ID is the only class that lacks it.
        """
        logger.exception("unhandled exception")
        return _envelope(request, 500, "Internal server error.")
