import logging
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HEADER = "X-Request-ID"

# Read by the log formatter, so any log line anywhere in the request carries
# the ID without every call site having to thread it through.
request_id_var: ContextVar[str] = ContextVar("request_id", default="")

logger = logging.getLogger("pikol.request")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Tag every request, echo the caller's ID if it sent one (spec §3.5)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get(HEADER) or str(uuid4())
        token = request_id_var.set(request_id)
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "%s %s %s %sms",
                request.method,
                request.url.path,
                response.status_code,
                elapsed_ms,
            )
            response.headers[HEADER] = request_id
            return response
        finally:
            request_id_var.reset(token)
