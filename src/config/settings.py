from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment configuration, read once at boot.

    Distinct from `parameters` (src/parameters/), which holds *business*
    rules — prices, windows, caps — in Postgres so they are tunable without
    a deploy. Anything here requires a restart to change.
    """

    # `extra="ignore"` because the process environment always carries
    # unrelated variables (PATH, HOME). Unknown keys are not an error.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "pikol"
    debug: bool = False
    database_url: str
    cors_allowed_origins: list[str] = []

    # HS256 signing key. 32 characters minimum: a short key lets anyone holding
    # one captured token brute-force it offline and mint a SUPER_ADMIN.
    jwt_secret: SecretStr = Field(min_length=32)
    access_token_minutes: int = 15
    refresh_token_days: int = 7

    # Where email links point. No trailing slash — links are built as
    # f"{frontend_base_url}/verify-email?token=...".
    frontend_base_url: str = "http://localhost:3000"
    # "console" logs each email (development); "memory" collects them for
    # tests. A real sender arrives before go-live (Phase 9).
    email_provider: Literal["console", "memory"] = "console"
    rate_limit_enabled: bool = True

    # Payments (ADR 0002). Only the mock exists until the Maya adapter lands,
    # so config cannot select an adapter that is not there.
    payment_provider: Literal["mock"] = "mock"
    # Where this API is reachable — the mock's checkout URLs point here.
    api_base_url: str = "http://localhost:8000"
    # Mounts the mock's hosted page and "simulate payment" routes. Off by
    # default: without them nobody can mark a mock payment paid, so a deploy
    # accidentally left on the mock cannot give courts away.
    mock_checkout_enabled: bool = False
    # Maya's published webhook source IPs (ADR 0002). Empty = no allowlist.
    # Defence in depth only — the re-fetch is the real control.
    maya_webhook_allowed_ips: list[str] = []
    # Proxies in front of the app that append to X-Forwarded-For (Render: 1).
    trusted_proxy_hops: int = Field(default=0, ge=0)

    @field_validator("frontend_base_url", "api_base_url")
    @classmethod
    def reject_base_url_trailing_slash(cls, url: str) -> str:
        if url.endswith("/"):
            raise ValueError(
                f"base URL {url!r} has a trailing slash; links built from it "
                f"would come out with a double slash"
            )
        return url

    @field_validator("cors_allowed_origins")
    @classmethod
    def reject_trailing_slash(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            if origin.endswith("/"):
                raise ValueError(
                    f"CORS origin {origin!r} has a trailing slash; browsers send "
                    f"the origin without one, so it would never match"
                )
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
