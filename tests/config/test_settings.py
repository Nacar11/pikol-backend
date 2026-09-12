import pytest
from pydantic import ValidationError

from src.config.settings import Settings


def test_settings_load_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/pikol")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", '["http://localhost:3000"]')

    settings = Settings(_env_file=None)

    assert settings.database_url == "postgresql+asyncpg://u:p@localhost:5432/pikol"
    assert settings.cors_allowed_origins == ["http://localhost:3000"]
    assert settings.debug is False


def test_missing_database_url_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing connection string must stop the process at boot, not surface
    as a confusing connection error on the first request."""
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)

    assert "database_url" in str(exc.value)


def test_cors_origins_reject_trailing_slashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A trailing slash silently breaks CORS matching in production — the
    exact trap asima hit. Reject it at boot instead."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/pikol")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", '["http://localhost:3000/"]')

    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None)

    assert "trailing slash" in str(exc.value)
