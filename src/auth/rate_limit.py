from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from src.config.settings import get_settings
from src.utils.errors import ErrorResponse

# Keyed by client IP, held in process memory — correct for the single Render
# instance this runs as. Behind Render's proxy, uvicorn must run with
# --proxy-headers --forwarded-allow-ips, or every request appears to come
# from the proxy and all clients share one bucket (Phase 9).
limiter = Limiter(key_func=get_remote_address, enabled=get_settings().rate_limit_enabled)

LOGIN_LIMIT = "10/minute"
EMAIL_LIMIT = "5/minute"  # register, verification resend, forgot-password


async def handle_rate_limited(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimitExceeded)
    request_id = getattr(request.state, "request_id", "")
    body = ErrorResponse(
        detail="Too many attempts. Wait a minute and try again.",
        code="RATE_LIMITED",
        request_id=request_id,
    )
    return JSONResponse(
        status_code=429, content=body.model_dump(), headers={"X-Request-ID": request_id}
    )
