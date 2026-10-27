from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.domain.rules import new_reference_code
from src.bookings.persistence.repository import REFERENCE_ATTEMPTS
from src.checkouts.persistence.models import CHECKOUT_REFERENCE_CONSTRAINT, CheckoutModel
from src.payments.domain.enums import PaymentOutcome
from src.utils.errors import constraint_of


class CheckoutRepository:
    """Owns every write to checkouts.status — `set_status` is the one writer,
    as `BookingRepository.set_status` is for booking_status.

    Customer reads take `customer_id` from the token: it IS the scope.
    """

    async def insert(self, session: AsyncSession, *, fields: dict[str, Any]) -> CheckoutModel:
        for _ in range(REFERENCE_ATTEMPTS):
            checkout = CheckoutModel(reference_code=new_reference_code(), **fields)
            try:
                async with session.begin_nested():
                    session.add(checkout)
                    await session.flush()
            except IntegrityError as exc:
                if constraint_of(exc) == CHECKOUT_REFERENCE_CONSTRAINT:
                    continue
                raise
            return checkout
        raise RuntimeError("could not allocate a unique checkout reference")

    async def set_status(
        self, session: AsyncSession, checkout: CheckoutModel, status: PaymentOutcome
    ) -> None:
        await session.execute(
            update(CheckoutModel).where(CheckoutModel.id == checkout.id).values(status=status)
        )
        await session.refresh(checkout, attribute_names=["status", "updated_at"])

    async def count_live(self, session: AsyncSession, customer_id: int, now: datetime) -> int:
        """Live holds only. Counting PENDING alone would include holds that
        lapsed weeks ago and lock an abandoning customer out for good."""
        return (
            await session.execute(
                select(func.count()).where(
                    CheckoutModel.customer_id == customer_id,
                    CheckoutModel.status == PaymentOutcome.PENDING,
                    CheckoutModel.expires_at > now,
                )
            )
        ).scalar_one()

    async def list_for_customer(
        self, session: AsyncSession, customer_id: int, *, page: int, limit: int
    ) -> tuple[list[CheckoutModel], int]:
        mine = CheckoutModel.customer_id == customer_id
        total = (await session.execute(select(func.count()).where(mine))).scalar_one()
        rows = await session.execute(
            select(CheckoutModel)
            .where(mine)
            .order_by(CheckoutModel.created_at.desc(), CheckoutModel.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        return list(rows.unique().scalars().all()), total

    async def get_for_customer(
        self, session: AsyncSession, checkout_id: int, customer_id: int
    ) -> CheckoutModel | None:
        result = await session.execute(
            select(CheckoutModel).where(
                CheckoutModel.id == checkout_id, CheckoutModel.customer_id == customer_id
            )
        )
        return result.unique().scalar_one_or_none()

    async def get_for_update(self, session: AsyncSession, checkout_id: int) -> CheckoutModel:
        """Lock only the checkout row: the eager-loaded customer and venue
        are outer joins, which Postgres refuses to lock."""
        result = await session.execute(
            select(CheckoutModel)
            .where(CheckoutModel.id == checkout_id)
            .with_for_update(of=CheckoutModel)
            .execution_options(populate_existing=True)
        )
        return result.unique().scalar_one()


checkout_repository = CheckoutRepository()
