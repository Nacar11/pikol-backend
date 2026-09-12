import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

logger = logging.getLogger("pikol.error")

SLOT_CONSTRAINT = "uq_court_slot_active"


class ErrorResponse(BaseModel):
    detail: str
    request_id: str


def _envelope(request: Request, status: int, detail: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "")
    return JSONResponse(
        status_code=status,
        content=ErrorResponse(detail=detail, request_id=request_id).model_dump(),
        headers={"X-Request-ID": request_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
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
        cause = getattr(exc.orig, "__cause__", None)
        if getattr(cause, "constraint_name", None) == SLOT_CONSTRAINT:
            return _envelope(request, 409, "That slot is no longer available.")

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
