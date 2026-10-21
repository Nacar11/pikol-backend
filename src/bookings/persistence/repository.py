from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, func, not_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.scope import VenueScope
from src.bookings.domain.enums import RELEASING, BookingStatus, BookingType
from src.bookings.domain.rules import new_reference_code
from src.bookings.persistence.models import (
    REFERENCE_CONSTRAINT,
    SLOT_CONSTRAINT,
    BookingGroupModel,
    BookingSlotModel,
)
from src.courts.persistence.models import CourtModel
from src.utils.errors import AppError, constraint_of

REFERENCE_ATTEMPTS = 5


def slot_unavailable() -> AppError:
    return AppError(409, "SLOT_UNAVAILABLE", "That slot is no longer available.")


class BookingRepository:
    """Owns every write to booking_status.

    booking_slots.booking_status mirrors its group and must change in the
    same transaction, never independently (spec §4.2). That invariant has
    exactly one implementation — `set_status` (plus `insert` and
    `expire_lapsed`, which write both tables together). No other code may
    assign booking_status.
    """

    async def insert(
        self,
        session: AsyncSession,
        *,
        fields: dict[str, Any],
        slot_prices: Sequence[tuple[datetime, int]],
    ) -> BookingGroupModel:
        """Insert a group and its slots; the partial unique index decides
        collisions (spec §4.1). A taken slot is a 409; a reference-code clash
        is retried with a fresh code."""
        for _ in range(REFERENCE_ATTEMPTS):
            group = BookingGroupModel(reference_code=new_reference_code(), **fields)
            group.slots = [
                BookingSlotModel(
                    court_id=group.court_id,
                    starts_at=starts_at,
                    price_centavos=price,
                    booking_status=group.booking_status,
                )
                for starts_at, price in slot_prices
            ]
            try:
                async with session.begin_nested():
                    session.add(group)
                    await session.flush()
            except IntegrityError as exc:
                name = constraint_of(exc)
                if name == SLOT_CONSTRAINT:
                    raise slot_unavailable() from None
                if name == REFERENCE_CONSTRAINT:
                    continue
                raise
            return group
        raise RuntimeError("could not allocate a unique booking reference")

    async def set_status(
        self,
        session: AsyncSession,
        group: BookingGroupModel,
        status: BookingStatus,
        **group_fields: Any,
    ) -> BookingGroupModel:
        """THE status writer. Group and every slot, one transaction.

        Leaving PENDING clears expires_at, because ck_pending_has_expiry says
        only a hold may have a deadline.
        """
        values: dict[str, Any] = {"booking_status": status, **group_fields}
        if status != BookingStatus.PENDING:
            values["expires_at"] = None
        await session.execute(
            update(BookingGroupModel).where(BookingGroupModel.id == group.id).values(**values)
        )
        await session.execute(
            update(BookingSlotModel)
            .where(BookingSlotModel.booking_group_id == group.id)
            .values(booking_status=status)
        )
        await session.refresh(group)
        for slot in group.slots:
            await session.refresh(slot)
        return group

    async def expire_lapsed(
        self,
        session: AsyncSession,
        court_id: int,
        starts_ats: Sequence[datetime],
        now: datetime,
    ) -> list[int]:
        """Lazy hold cleanup on the write path (spec §4.3).

        Expires every lapsed hold touching exactly these (court, hour) pairs
        — and ALL slots of each such group, since the group is the unit that
        was never paid for. The UPDATE re-checks PENDING and the deadline, so
        a group confirmed in between is never expired.
        """
        candidates = (
            (
                await session.execute(
                    select(BookingSlotModel.booking_group_id)
                    .join(
                        BookingGroupModel, BookingGroupModel.id == BookingSlotModel.booking_group_id
                    )
                    .where(
                        BookingSlotModel.court_id == court_id,
                        BookingSlotModel.starts_at.in_(starts_ats),
                        BookingGroupModel.booking_status == BookingStatus.PENDING,
                        BookingGroupModel.expires_at <= now,
                    )
                    .distinct()
                )
            )
            .scalars()
            .all()
        )
        if not candidates:
            return []
        expired = (
            (
                await session.execute(
                    update(BookingGroupModel)
                    .where(
                        BookingGroupModel.id.in_(candidates),
                        BookingGroupModel.booking_status == BookingStatus.PENDING,
                        BookingGroupModel.expires_at <= now,
                    )
                    .values(booking_status=BookingStatus.EXPIRED, expires_at=None)
                    .returning(BookingGroupModel.id)
                )
            )
            .scalars()
            .all()
        )
        if expired:
            await session.execute(
                update(BookingSlotModel)
                .where(BookingSlotModel.booking_group_id.in_(expired))
                .values(booking_status=BookingStatus.EXPIRED)
            )
        return list(expired)

    # ── reads ────────────────────────────────────────────────────────────

    async def count_live_holds(self, session: AsyncSession, player_id: int, now: datetime) -> int:
        """Unexpired holds only. Counting PENDING alone would include holds
        that lapsed weeks ago, and a player who abandoned checkout three
        times would be locked out forever (spec §4.3)."""
        return (
            await session.execute(
                select(func.count()).where(
                    BookingGroupModel.player_id == player_id,
                    BookingGroupModel.booking_status == BookingStatus.PENDING,
                    BookingGroupModel.expires_at > now,
                )
            )
        ).scalar_one()

    async def occupancy(
        self,
        session: AsyncSession,
        court_ids: Sequence[int],
        start: datetime,
        end: datetime,
        now: datetime,
        *,
        scope: VenueScope,
    ) -> dict[tuple[int, datetime], BookingType]:
        """Which (court, hour) pairs are taken in [start, end), and by what.

        Applies the hold-expiry filter: a lapsed PENDING row still exists
        (cleanup is lazy) but must not block the grid.
        """
        rows = await session.execute(
            select(BookingSlotModel.court_id, BookingSlotModel.starts_at, BookingGroupModel.type)
            .join(BookingGroupModel, BookingGroupModel.id == BookingSlotModel.booking_group_id)
            .join(CourtModel, CourtModel.id == BookingSlotModel.court_id)
            .where(
                BookingSlotModel.court_id.in_(court_ids),
                BookingSlotModel.starts_at >= start,
                BookingSlotModel.starts_at < end,
                BookingSlotModel.booking_status.not_in(RELEASING),
                not_(
                    and_(
                        BookingGroupModel.booking_status == BookingStatus.PENDING,
                        BookingGroupModel.expires_at <= now,
                    )
                ),
                scope.where(CourtModel.venue_id),
            )
        )
        return {(court_id, starts_at): type_ for court_id, starts_at, type_ in rows}

    # Player self-service: `player_id` comes from the token and IS the scope.

    async def list_for_player(
        self, session: AsyncSession, player_id: int, *, page: int, limit: int
    ) -> tuple[list[BookingGroupModel], int]:
        mine = BookingGroupModel.player_id == player_id
        total = (await session.execute(select(func.count()).where(mine))).scalar_one()
        rows = await session.execute(
            select(BookingGroupModel)
            .where(mine)
            .order_by(BookingGroupModel.created_at.desc(), BookingGroupModel.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(rows.scalars().unique().all()), total

    async def get_for_player(
        self, session: AsyncSession, booking_id: int, player_id: int
    ) -> BookingGroupModel | None:
        result = await session.execute(
            select(BookingGroupModel).where(
                BookingGroupModel.id == booking_id, BookingGroupModel.player_id == player_id
            )
        )
        return result.unique().scalar_one_or_none()

    # Staff reads: venue-owned, so `scope` is keyword-only with no default,
    # applied to the booking's own court's venue.

    @staticmethod
    def _first_slot() -> ColumnElement[datetime]:
        return (
            select(func.min(BookingSlotModel.starts_at))
            .where(BookingSlotModel.booking_group_id == BookingGroupModel.id)
            .correlate(BookingGroupModel)
            .scalar_subquery()
        )

    async def list_scoped(
        self,
        session: AsyncSession,
        *,
        scope: VenueScope,
        page: int,
        limit: int,
        venue_id: int | None = None,
        court_id: int | None = None,
        between: tuple[datetime, datetime] | None = None,
        booking_status: BookingStatus | None = None,
        type: BookingType | None = None,
    ) -> tuple[list[BookingGroupModel], int]:
        filters: list[Any] = [scope.where(CourtModel.venue_id)]
        if venue_id is not None:
            filters.append(CourtModel.venue_id == venue_id)
        if court_id is not None:
            filters.append(BookingGroupModel.court_id == court_id)
        if booking_status is not None:
            filters.append(BookingGroupModel.booking_status == booking_status)
        if type is not None:
            filters.append(BookingGroupModel.type == type)
        if between is not None:
            # Any slot on that (Manila) day — a session crossing midnight
            # appears on both days.
            start, end = between
            filters.append(
                exists().where(
                    BookingSlotModel.booking_group_id == BookingGroupModel.id,
                    BookingSlotModel.starts_at >= start,
                    BookingSlotModel.starts_at < end,
                )
            )
        base = select(BookingGroupModel).join(
            CourtModel, CourtModel.id == BookingGroupModel.court_id
        )
        total = (
            await session.execute(select(func.count()).select_from(base.where(*filters).subquery()))
        ).scalar_one()
        rows = await session.execute(
            base.where(*filters)
            .order_by(self._first_slot(), BookingGroupModel.id)
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(rows.unique().scalars().all()), total

    async def get_scoped(
        self, session: AsyncSession, booking_id: int, *, scope: VenueScope
    ) -> BookingGroupModel | None:
        result = await session.execute(
            select(BookingGroupModel)
            .join(CourtModel, CourtModel.id == BookingGroupModel.court_id)
            .where(BookingGroupModel.id == booking_id, scope.where(CourtModel.venue_id))
        )
        return result.unique().scalar_one_or_none()


booking_repository = BookingRepository()
