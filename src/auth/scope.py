from dataclasses import dataclass

from fastapi import Depends
from sqlalchemy import ColumnElement, SQLColumnExpression, true

from src.auth.dependencies import forbidden, get_current_user
from src.users.persistence.models import UserModel


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

    def where(self, column: SQLColumnExpression[int | None]) -> ColumnElement[bool]:
        if self.all_venues:
            return true()
        return column == self.venue_id


async def get_venue_scope(user: UserModel = Depends(get_current_user)) -> VenueScope:
    if user.system_admin:
        return VenueScope(all_venues=True, venue_id=None)
    if user.venue_id is not None:
        return VenueScope(all_venues=False, venue_id=user.venue_id)
    raise forbidden()  # a player has no venue scope at all
