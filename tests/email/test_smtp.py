from typing import Any

import pytest

from src.email import adapters
from src.email.adapters import SmtpEmailSender
from src.email.port import EmailMessage


class FakeSMTP:
    """Stands in for smtplib.SMTP and records the conversation."""

    sessions: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.address = (host, port)
        self.calls: list[str] = []
        self.sent: Any = None
        FakeSMTP.sessions.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *_: object) -> None:
        self.calls.append("quit")

    def starttls(self) -> None:
        self.calls.append("starttls")

    def login(self, username: str, password: str) -> None:
        self.calls.append(f"login:{username}:{password}")

    def send_message(self, message: Any) -> None:
        self.calls.append("send")
        self.sent = message


async def test_smtp_sends_over_starttls_with_login(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeSMTP.sessions = []
    monkeypatch.setattr(adapters.smtplib, "SMTP", FakeSMTP)
    sender = SmtpEmailSender("smtp.example.com", 587, "user", "pw", "Pikol <b@example.com>")

    await sender.send(EmailMessage(to="kim@example.com", subject="Hi", body="Link: x"))

    (smtp,) = FakeSMTP.sessions
    assert smtp.address == ("smtp.example.com", 587)
    assert smtp.calls == ["starttls", "login:user:pw", "send", "quit"]
    assert (smtp.sent["From"], smtp.sent["To"], smtp.sent["Subject"]) == (
        "Pikol <b@example.com>",
        "kim@example.com",
        "Hi",
    )
    assert smtp.sent.get_content().strip() == "Link: x"
