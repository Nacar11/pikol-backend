from pydantic import BaseModel, ConfigDict, Field

from src.venues.persistence.models import VenueModel


class VenueOut(BaseModel):
    id: int
    name: str
    address: str | None
    is_active: bool

    @classmethod
    def of(cls, venue: VenueModel) -> "VenueOut":
        return cls(id=venue.id, name=venue.name, address=venue.address, is_active=venue.is_active)


class VenueCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=128)
    address: str | None = Field(default=None, max_length=1000)


class VenueUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=128)
    address: str | None = Field(default=None, max_length=1000)
    is_active: bool | None = None
