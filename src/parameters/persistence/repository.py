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

    async def lock_all(self, session: AsyncSession) -> dict[str, ParameterModel]:
        """Every row, locked FOR UPDATE until the transaction ends. A write
        validates against the whole table, so concurrent edits must serialise."""
        result = await session.execute(
            select(ParameterModel)
            .order_by(ParameterModel.key)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return {row.key: row for row in result.scalars().all()}
