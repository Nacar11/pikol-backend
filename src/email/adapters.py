import logging

from src.email.port import EmailMessage, EmailSender

logger = logging.getLogger("pikol.email")


class ConsoleEmailSender:
    """Development: writes each email to the log instead of sending it.

    The body is logged because the link in it IS the point in development —
    it is how you verify an account locally. Never select this adapter in
    production: it would put live tokens in the log.
    """

    async def send(self, message: EmailMessage) -> None:
        logger.info("email to=%s subject=%r\n%s", message.to, message.subject, message.body)


class MemoryEmailSender:
    """Tests: collects every message so a test can read the link back."""

    def __init__(self) -> None:
        self.outbox: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.outbox.append(message)


ADAPTERS: dict[str, EmailSender] = {
    "console": ConsoleEmailSender(),
    "memory": MemoryEmailSender(),
}
