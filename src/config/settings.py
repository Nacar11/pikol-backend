from functools import lru_cache

from pydantic import field_validator
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
