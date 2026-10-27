from datetime import datetime

from src.email.templates import ReceiptLine, receipt_email
from src.utils.time import MANILA


def test_receipt_lists_every_court_and_the_total() -> None:
    message = receipt_email(
        to="kim@example.com",
        first_name="Kim",
        reference="PKL-AB234",
        venue="Riverside",
        lines=[
            ReceiptLine("Court 1", datetime(2026, 10, 29, 18, tzinfo=MANILA), 2, 100000),
            ReceiptLine("Court 2", datetime(2026, 10, 30, 7, tzinfo=MANILA), 1, 50000),
        ],
        fee_centavos=4500,
        total_centavos=154500,
    )

    assert message.subject == "Your Pikol receipt — PKL-AB234"
    assert "Riverside" in message.body
    assert "Court 1 — Thu 29 Oct 2026, 06:00 PM, 2 hours — ₱1,000.00" in message.body
    assert "Court 2 — Fri 30 Oct 2026, 07:00 AM, 1 hour — ₱500.00" in message.body
    assert "₱45.00" in message.body
    assert "₱1,545.00" in message.body
    assert "Paid bookings are final." in message.body
