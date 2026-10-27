from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.config.settings import get_settings
from src.email.port import EmailMessage


def _link(path: str, token: str) -> str:
    return f"{get_settings().frontend_base_url}{path}?token={token}"


def verification_email(to: str, first_name: str, token: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Verify your Pikol email",
        body=(
            f"Hi {first_name},\n\n"
            f"Confirm your email to start booking courts:\n"
            f"{_link('/verify-email', token)}\n\n"
            f"The link expires in 24 hours. If you did not sign up, ignore this email."
        ),
    )


def password_reset_email(to: str, first_name: str, token: str) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="Reset your Pikol password",
        body=(
            f"Hi {first_name},\n\n"
            f"Set a new password here:\n"
            f"{_link('/reset-password', token)}\n\n"
            f"The link expires in 60 minutes. If you did not ask for this, ignore this email."
        ),
    )


def invitation_email(to: str, first_name: str, token: str, expiry_days: int) -> EmailMessage:
    return EmailMessage(
        to=to,
        subject="You have been invited to Pikol",
        body=(
            f"Hi {first_name},\n\n"
            f"You have been added as staff on Pikol. Set your password to "
            f"activate your account:\n"
            f"{_link('/accept-invite', token)}\n\n"
            f"The invitation expires in {expiry_days} days."
        ),
    )


def _pesos(centavos: int) -> str:
    return f"₱{Decimal(centavos) / 100:,.2f}"


@dataclass(frozen=True)
class ReceiptLine:
    court: str
    starts_at_manila: datetime
    hours: int
    price_centavos: int


def receipt_email(
    *,
    to: str,
    first_name: str,
    reference: str,
    venue: str,
    lines: Sequence[ReceiptLine],
    fee_centavos: int,
    total_centavos: int,
) -> EmailMessage:
    """One receipt per checkout: every court and hour it paid for."""
    booked = "\n".join(
        f"  {line.court} — {line.starts_at_manila.strftime('%a %d %b %Y, %I:%M %p')}, "
        f"{line.hours} hour{'s' if line.hours != 1 else ''} — {_pesos(line.price_centavos)}"
        for line in lines
    )
    return EmailMessage(
        to=to,
        subject=f"Your Pikol receipt — {reference}",
        body=(
            f"Hi {first_name},\n\n"
            f"Your booking at {venue} is confirmed.\n\n"
            f"Reference:  {reference}\n"
            f"Times are Manila time.\n\n"
            f"{booked}\n\n"
            f"Convenience fee:  {_pesos(fee_centavos)}\n"
            f"Total paid:       {_pesos(total_centavos)}\n\n"
            f"Paid bookings are final. Show this reference at the counter. See you on court!"
        ),
    )
