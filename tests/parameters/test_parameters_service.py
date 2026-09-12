from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.database.session import SessionLocal
from src.parameters.persistence.repository import ParameterRepository
from src.parameters.service import ParameterService


async def test_loads_seeded_parameters_from_the_database() -> None:
    service = ParameterService()

    async with SessionLocal() as session:
        params = await service.get(session)

    assert params.default_slot_price_centavos == 50000
    assert params.convenience_fee_percent == Decimal("3.00")
    assert params.min_lead_minutes >= params.hold_duration_minutes


class CountingRepository(ParameterRepository):
    """Injected rather than monkeypatched — the service takes its repository
    as a constructor argument, so the test needs no patching and no
    type: ignore."""

    def __init__(self) -> None:
        self.calls = 0

    async def load_all(self, session: AsyncSession) -> dict[str, str]:
        self.calls += 1
        return await super().load_all(session)


async def test_second_call_within_ttl_does_not_query_again() -> None:
    """The accessor is read on every booking; hitting Postgres each time is
    waste. The TTL is short so an admin edit still lands quickly."""
    repository = CountingRepository()
    service = ParameterService(repository, ttl_seconds=60)

    async with SessionLocal() as session:
        await service.get(session)
        await service.get(session)

    assert repository.calls == 1


async def test_expired_cache_reloads() -> None:
    repository = CountingRepository()
    service = ParameterService(repository, ttl_seconds=0)

    async with SessionLocal() as session:
        first = await service.get(session)
        second = await service.get(session)

    assert repository.calls == 2
    assert first == second
