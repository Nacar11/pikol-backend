from datetime import UTC, datetime

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from src.parameters.domain.parameters import Parameters
from src.parameters.persistence.models import ParameterModel
from src.parameters.persistence.repository import ParameterRepository
from src.parameters.service import parameter_service
from src.users.persistence.models import UserModel
from src.utils.errors import AppError


class ParameterAdminService:
    def __init__(self, repository: ParameterRepository | None = None) -> None:
        self._repository = repository or ParameterRepository()

    async def update(
        self, session: AsyncSession, caller: UserModel, key: str, value: str
    ) -> ParameterModel:
        rows = await self._repository.lock_all(session)
        row = rows.get(key)
        if row is None:
            raise AppError(404, "NOT_FOUND", "Parameter not found.")

        # Validate the WHOLE model with the candidate value, so a cross-field
        # rule (min_lead_minutes >= hold_duration_minutes) is enforced here.
        candidate = {**{k: r.value for k, r in rows.items()}, key: value}
        try:
            Parameters.model_validate(candidate)
        except ValidationError as exc:
            first = exc.errors()[0]
            raise AppError(422, "INVALID_PARAMETER", f"{key}: {first['msg']}") from exc

        row.value = value
        row.updated_at = datetime.now(UTC)
        row.updated_by_user_id = caller.id
        await session.commit()
        parameter_service.invalidate()
        return row


parameter_admin_service = ParameterAdminService()
