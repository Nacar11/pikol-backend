from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.courts.dto import CourtCreate, CourtUpdate
from src.courts.persistence.models import CourtModel
from src.courts.persistence.repository import court_repository
from src.users.persistence.models import UserModel
from src.utils.errors import AppError
from src.venues.service import venue_service


def court_not_found() -> AppError:
    return AppError(404, "NOT_FOUND", "Court not found.")


class CourtService:
    async def get(self, session: AsyncSession, court_id: int, *, scope: VenueScope) -> CourtModel:
        """Ownership after fetch: out of scope is 404, never 403 (spec §6.2)."""
        court = await court_repository.get(session, court_id, scope=scope)
        if court is None:
            raise court_not_found()
        return court

    async def create(
        self,
        session: AsyncSession,
        caller: UserModel,
        venue_id: int,
        body: CourtCreate,
        *,
        scope: VenueScope,
    ) -> CourtModel:
        # The venue must be in scope — the 404 for Venue B comes from here.
        await venue_service.get(session, venue_id, scope=scope, active_only=False)
        court = CourtModel(
            venue_id=venue_id,
            **body.model_dump(),
            created_by_user_id=caller.id,
            updated_by_user_id=caller.id,
        )
        session.add(court)
        await session.commit()
        await session.refresh(court)
        return court

    async def update(
        self,
        session: AsyncSession,
        caller: UserModel,
        court_id: int,
        body: CourtUpdate,
        *,
        scope: VenueScope,
    ) -> CourtModel:
        court = await self.get(session, court_id, scope=scope)
        for field, value in body.model_dump(exclude_unset=True).items():
            setattr(court, field, value)
        court.updated_by_user_id = caller.id
        await session.commit()
        await session.refresh(court)
        return court


court_service = CourtService()
