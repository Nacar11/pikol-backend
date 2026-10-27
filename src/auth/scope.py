from dataclasses import dataclass

from sqlalchemy import ColumnElement, SQLColumnExpression, true


@dataclass(frozen=True)
class VenueScope:
    """Which venues the caller may see, resolved once from the token.

    Every repository method touching venue-owned data takes this as a
    REQUIRED keyword argument with no default (spec §6.2). The failure mode
    designed against is forgetting: a missing filter looks entirely normal in
    review, while a missing argument is a TypeError the first time it runs.
    """

    all_venues: bool  # SUPER_ADMIN
    venue_id: int | None  # VENUE_MANAGER / VENUE_STAFF

    @classmethod
    def everything(cls) -> "VenueScope":
        """For the player-facing catalog, which deliberately spans every
        venue. Passed explicitly so "unscoped" is a visible, greppable
        decision at the call site — never an omission."""
        return cls(all_venues=True, venue_id=None)

    def where(self, column: SQLColumnExpression[int | None]) -> ColumnElement[bool]:
        if self.all_venues:
            return true()
        return column == self.venue_id
