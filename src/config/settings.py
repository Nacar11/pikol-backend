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

    @field_validator("frontend_base_url")
    @classmethod
    def reject_base_url_trailing_slash(cls, url: str) -> str:
        if url.endswith("/"):
            raise ValueError(
                f"FRONTEND_BASE_URL {url!r} has a trailing slash; email links "
                f"would come out as //verify-email"
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
