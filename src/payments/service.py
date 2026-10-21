import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.bookings.persistence.models import BookingGroupModel
from src.config.settings import get_settings
from src.payments.domain.port import PaymentProvider, ProviderError
from src.payments.persistence.repository import payment_repository
from src.utils.errors import AppError

logger = logging.getLogger("pikol.payments")


def provider_unavailable() -> AppError:
    return AppError(
        502,
        "PAYMENT_PROVIDER_UNAVAILABLE",
        "We could not reach the payment provider. Your slot is held — try paying again.",
    )


class PaymentService:
    async def start_checkout(
        self, session: AsyncSession, group: BookingGroupModel, provider: PaymentProvider
    ) -> str:
        """Open a checkout for a hold that is ALREADY COMMITTED.

        The provider call sits outside the booking transaction on purpose:
        holding one open across a network round-trip is how connection pools
        die (spec §5.3). If it fails, the hold stands and expires on its own.
        """
        try:
            checkout = await provider.create_checkout(
                amount_centavos=group.total_centavos,
                reference=group.reference_code,
                description=f"Pikol court booking {group.reference_code}",
                return_url=(
                    f"{get_settings().frontend_base_url}/checkout/return?booking_id={group.id}"
                ),
            )
        except ProviderError:
            logger.warning("checkout creation failed: booking=%s", group.reference_code)
            raise provider_unavailable() from None
        await payment_repository.add_checkout(
            session,
            booking_group_id=group.id,
            provider=provider.name,
            checkout=checkout,
            amount_centavos=group.total_centavos,
        )
        await session.commit()
        return checkout.checkout_url


payment_service = PaymentService()
