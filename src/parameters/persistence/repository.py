from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.parameters.persistence.models import ParameterModel


class ParameterRepository:
    async def load_all(self, session: AsyncSession) -> dict[str, str]:
        result = await session.execute(select(ParameterModel))
        return {row.key: row.value for row in result.scalars().all()}
