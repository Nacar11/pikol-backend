from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.users.persistence.models import UserModel
from src.utils.errors import AppError
from src.venues.dto import VenueCreate, VenueUpdate
from src.venues.persistence.models import VenueModel
from src.venues.persistence.repository import venue_repository


def venue_not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Venue not found.")


class VenueService:
    async def get(
        self, session: AsyncSession, venue_id: int, *, scope: VenueScope, active_only: bool
    ) -> VenueModel:
        venue = await venue_repository.get(session, venue_id, scope=scope, active_only=active_only)
        if venue is None:
            raise venue_not_found()
        return venue

    async def create(
        self, session: AsyncSession, caller: UserModel, body: VenueCreate
    ) -> VenueModel:
        venue = VenueModel(
            **body.model_dump(), created_by_user_id=caller.id, updated_by_user_id=caller.id
        )
        session.add(venue)
        await session.commit()
        return venue

    async def update(
        self,
        session: AsyncSession,
        caller: UserModel,
        venue_id: int,
        body: VenueUpdate,
        *,
        scope: VenueScope,
    ) -> VenueModel:
        venue = await self.get(session, venue_id, scope=scope, active_only=False)
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(venue, field, value)
        venue.updated_by_user_id = caller.id
        await session.commit()
        await session.refresh(venue)
        return venue


venue_service = VenueService()
