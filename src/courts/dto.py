from pydantic import BaseModel, ConfigDict, Field

from src.courts.persistence.models import CourtModel


class CourtOut(BaseModel):
    id: int
    venue_id: int
    name: str
    sort_order: int
    is_active: bool

    @classmethod
    def of(cls, court: CourtModel) -> "CourtOut":
        return cls(
            id=court.id,
            venue_id=court.venue_id,
            name=court.name,
            sort_order=court.sort_order,
            is_active=court.is_active,
        )


class CourtCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
    sort_order: int = Field(default=0, ge=0, le=10_000)


class CourtUpdate(BaseModel):
    """No venue_id: moving a court between venues would carry its bookings
    with it. extra="forbid" makes trying a 422."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    sort_order: int | None = Field(default=None, ge=0, le=10_000)
    is_active: bool | None = None
