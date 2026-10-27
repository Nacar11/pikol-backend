from pydantic import BaseModel


class CourtSummary(BaseModel):
    court_id: int
    # None when the caller cannot see it at this venue (no PRICE:Manage /
    # BOOKING:ReadAny), rather than a 403 for the whole card grid.
    rate_centavos: int | None
    booked_hours: int | None


class VenueSummary(BaseModel):
    venue_id: int
    active_courts: int
    booked_hours: int
