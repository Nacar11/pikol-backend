from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded

from src.config.settings import get_settings
from src.utils.errors import ErrorResponse
from src.utils.net import client_ip


def _client_key(request: Request) -> str:
    """The caller's IP behind TRUSTED_PROXY_HOPS proxies (Render: 1).

    Not the socket address — behind a proxy that is the proxy, and every
    client would share one bucket. Not uvicorn's --proxy-headers with
    --forwarded-allow-ips=* either — that trusts the LEFTMOST X-Forwarded-For
    entry, which the caller writes, so a fresh fake IP per attempt would
    never be limited.
    """
    return client_ip(request, get_settings().trusted_proxy_hops)


# Held in process memory — correct for the single Render instance this runs as.
limiter = Limiter(key_func=_client_key, enabled=get_settings().rate_limit_enabled)

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
