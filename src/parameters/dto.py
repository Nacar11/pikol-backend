from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.parameters.persistence.models import ParameterModel


class ParameterOut(BaseModel):
    key: str
    value: str
    value_type: str
    description: str
    updated_at: datetime
    updated_by_user_id: int | None

    @classmethod
    def of(cls, row: ParameterModel) -> "ParameterOut":
        return cls(
            key=row.key,
            value=row.value,
            value_type=row.value_type,
            description=row.description,
            updated_at=row.updated_at,
            updated_by_user_id=row.updated_by_user_id,
        )


class ParameterUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str = Field(min_length=1, max_length=64)
