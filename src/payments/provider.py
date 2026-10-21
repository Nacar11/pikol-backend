from functools import lru_cache

from src.config.settings import get_settings
from src.payments.adapters.mock import MockProvider
from src.payments.domain.port import PaymentProvider


@lru_cache
def get_mock_provider() -> MockProvider:
    """One process-wide mock: its "Maya" lives in this process's memory."""
    return MockProvider(api_base_url=get_settings().api_base_url)


def get_payment_provider() -> PaymentProvider:
    """FastAPI dependency. The adapter is chosen by PAYMENT_PROVIDER — config,
    never an `if (env)` in application code. Only "mock" exists today; the
    Maya adapter adds its branch when it lands."""
    match get_settings().payment_provider:
        case "mock":
            return get_mock_provider()
