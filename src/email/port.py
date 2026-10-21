import logging
from dataclasses import dataclass
from typing import Protocol

from src.config.settings import get_settings

logger = logging.getLogger("pikol.email")


@dataclass(frozen=True)
class EmailMessage:
    to: str
    subject: str
    body: str


class EmailSender(Protocol):
    async def send(self, message: EmailMessage) -> None: ...


def get_email_sender() -> EmailSender:
    """FastAPI dependency. The adapter is chosen by EMAIL_PROVIDER — config,
    never an `if (env)` in application code."""
    from src.email.adapters import ADAPTERS

    return ADAPTERS[get_settings().email_provider]


async def send_quietly(sender: EmailSender, message: EmailMessage) -> None:
    """Send after the transaction has committed; log a failure, never raise.

    The account already exists by then, and every email here has a resend
    path. Turning a mail outage into a 500 would tell the caller the
    registration failed when it did not.
    """
    try:
        await sender.send(message)
    except Exception:
        logger.exception("email send failed: subject=%r", message.subject)
