from starlette.requests import Request


def client_ip(request: Request, trusted_proxy_hops: int) -> str:
    """The caller's IP behind `trusted_proxy_hops` appending proxies.

    Each trusted proxy appends the address it received from, so the client
    is the hops-th entry FROM THE RIGHT. Reading the leftmost entry instead
    trusts a value the caller wrote themselves — the one-line mistake that
    makes an allowlist decorative (ADR 0002 §1).
    """
    if trusted_proxy_hops == 0:
        return request.client.host if request.client else ""
    hops = [h.strip() for h in request.headers.get("x-forwarded-for", "").split(",") if h.strip()]
    if len(hops) < trusted_proxy_hops:
        return ""
    return hops[-trusted_proxy_hops]
