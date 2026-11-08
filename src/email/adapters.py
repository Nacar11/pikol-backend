import asyncio
import logging
import smtplib
from email.message import EmailMessage as MimeMessage
from functools import lru_cache

from src.config.settings import get_settings
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


class SmtpEmailSender:
    """Production: one STARTTLS connection per email, through any relay.

    smtplib blocks, so it runs on a worker thread — never on the event loop,
    where a slow relay would stall every request.
    """

    def __init__(self, host: str, port: int, username: str, password: str, sender: str) -> None:
        self._host, self._port = host, port
        self._username, self._password = username, password
        self._sender = sender

    async def send(self, message: EmailMessage) -> None:
        await asyncio.to_thread(self._send, message)

    def _send(self, message: EmailMessage) -> None:
        mime = MimeMessage()
        mime["From"] = self._sender
        mime["To"] = message.to
        mime["Subject"] = message.subject
        mime.set_content(message.body)
        with smtplib.SMTP(self._host, self._port, timeout=10) as smtp:
            smtp.starttls()
            if self._username:
                smtp.login(self._username, self._password)
            smtp.send_message(mime)
        logger.info("email sent: subject=%r", message.subject)  # never the body: it holds tokens


@lru_cache
def smtp_sender() -> SmtpEmailSender:
    settings = get_settings()
    return SmtpEmailSender(
        settings.smtp_host,
        settings.smtp_port,
        settings.smtp_username,
        settings.smtp_password.get_secret_value(),
        settings.email_from,
    )


ADAPTERS: dict[str, EmailSender] = {
    "console": ConsoleEmailSender(),
    "memory": MemoryEmailSender(),
}
