from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.parameters.persistence.models import ParameterModel


class ParameterRepository:
    async def load_all(self, session: AsyncSession) -> dict[str, str]:
        result = await session.execute(select(ParameterModel))
        return {row.key: row.value for row in result.scalars().all()}

    async def list_rows(self, session: AsyncSession) -> list[ParameterModel]:
        result = await session.execute(select(ParameterModel).order_by(ParameterModel.key))
        return list(result.scalars().all())

    async def get_row(self, session: AsyncSession, key: str) -> ParameterModel | None:
        return await session.get(ParameterModel, key, populate_existing=True)
