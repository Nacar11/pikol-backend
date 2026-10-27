from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.access import VenueAccess
from src.auth.scope import VenueScope
from src.bookings.persistence.repository import booking_repository
from src.courts.persistence.repository import court_repository
from src.pricing.service import pricing_service
from src.summaries.dto import CourtSummary, VenueSummary
from src.utils.time import MANILA, manila_day_bounds
from src.venues.persistence.repository import venue_repository
from src.venues.service import venue_service


class SummaryService:
    async def courts(
        self, session: AsyncSession, venue_id: int, access: VenueAccess, now: datetime
    ) -> list[CourtSummary]:
        scope = access.scope
        await venue_service.get(session, venue_id, scope=scope, active_only=False)
        courts, _ = await court_repository.list_for_venue(
            session, venue_id, scope=scope, page=1, limit=100, active_only=False
        )
        ids = [c.id for c in courts]
        hour = now.replace(minute=0, second=0, microsecond=0)
        rates = None
        if "PRICE:Manage" in access.permissions:
            resolvers = await pricing_service.resolvers_for(session, ids, scope=scope)
            rates = {i: r.price_at(hour) for i, r in resolvers.items()}
        booked = None
        if "BOOKING:ReadAny" in access.permissions:
            start, end = manila_day_bounds(now.astimezone(MANILA).date())
            booked = await booking_repository.booked_hours(
                session, ids, start, end, now, scope=scope
            )
        return [
            CourtSummary(
                court_id=c.id,
                rate_centavos=rates[c.id] if rates is not None and c.is_active else None,
                booked_hours=None if booked is None else booked.get(c.id, 0),
            )
            for c in courts
        ]

    async def venues(
        self, session: AsyncSession, now: datetime, *, scope: VenueScope
    ) -> list[VenueSummary]:
        venues, _ = await venue_repository.list(
            session, scope=scope, page=1, limit=100, active_only=False
        )
        courts = await court_repository.list_all(session, scope=scope)
        start, end = manila_day_bounds(now.astimezone(MANILA).date())
        booked = await booking_repository.booked_hours(
            session, [c.id for c in courts], start, end, now, scope=scope
        )
        return [
            VenueSummary(
                venue_id=v.id,
                active_courts=sum(1 for c in courts if c.venue_id == v.id and c.is_active),
                booked_hours=sum(booked.get(c.id, 0) for c in courts if c.venue_id == v.id),
            )
            for v in venues
        ]


summary_service = SummaryService()
