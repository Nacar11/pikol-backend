import time

from sqlalchemy.ext.asyncio import AsyncSession

from src.parameters.domain.parameters import Parameters
from src.parameters.persistence.repository import ParameterRepository

DEFAULT_TTL_SECONDS = 30


class ParameterService:
    """TTL-cached access to the parameters table.

    Read on nearly every booking, so it is cached; the TTL is short so an
    admin changing the fee sees it take effect promptly without a deploy.
    """

    def __init__(
        self,
        repository: ParameterRepository | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._repository = repository or ParameterRepository()
        self._ttl = ttl_seconds
        self._cached: Parameters | None = None
        self._loaded_at = 0.0

    async def get(self, session: AsyncSession) -> Parameters:
        now = time.monotonic()
        if self._cached is not None and (now - self._loaded_at) < self._ttl:
            return self._cached

        self._cached = Parameters.model_validate(await self._repository.load_all(session))
        self._loaded_at = now
        return self._cached


parameter_service = ParameterService()
