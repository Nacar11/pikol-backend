from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.bookings.domain.errors import BookingError
from src.utils.errors import AppError, render

STATUS = {"invalid": 422, "conflict": 409}


def as_app_error(exc: BookingError) -> AppError:
    return AppError(STATUS[exc.kind], exc.code, exc.detail)


def register_booking_errors(app: FastAPI) -> None:
    """The domain raises HTTP-free BookingErrors; this is the one place they
    become status codes."""

    @app.exception_handler(BookingError)
    async def handle(request: Request, exc: BookingError) -> JSONResponse:
        return render(request, as_app_error(exc))
