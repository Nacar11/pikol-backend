from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.enums import BookingStatus
from src.bookings.domain.rules import new_reference_code
from src.bookings.persistence.models import (
    REFERENCE_CONSTRAINT,
    SLOT_CONSTRAINT,
    BookingGroupModel,
    BookingSlotModel,
)
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


booking_repository = BookingRepository()
