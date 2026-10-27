from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.bookings.persistence.models import BookingGroupModel
from src.database.base import Base
from src.payments.domain.enums import PaymentOutcome, RefundKind
from src.users.persistence.models import UserModel
from src.venues.persistence.models import VenueModel

CHECKOUT_REFERENCE_CONSTRAINT = "checkouts_reference_code_key"

payment_outcome = ENUM(*PaymentOutcome, name="payment_outcome", create_type=False)
refund_kind = ENUM(*RefundKind, name="refund_kind", create_type=False)


class CheckoutModel(Base):
    """One per "Pay now": the unit of PAYMENT. Its booking groups stay the
    unit of OCCUPANCY (one court each, ADR 0001) and share its expires_at,
    so they are held and released together.

    The PENDING trap applies: a lapsed hold keeps status PENDING until
    something writes it, so PENDING never means live without
    `expires_at > now`.
    """

    __tablename__ = "checkouts"
    __table_args__ = (
        Index("ix_checkouts_customer_id_status", "customer_id", "status"),
        Index("ix_checkouts_status_expires_at", "status", "expires_at"),
        CheckConstraint(
            "price_centavos >= 0 AND fee_centavos >= 0 "
            "AND total_centavos = price_centavos + fee_centavos",
            name="ck_checkout_money",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    venue_id: Mapped[int] = mapped_column(
        ForeignKey("venues.id", ondelete="RESTRICT"), nullable=False
    )
    # Only CheckoutRepository.set_status writes it.
    status: Mapped[PaymentOutcome] = mapped_column(payment_outcome, nullable=False)
    # Frozen at creation, like every booking price.
    price_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    fee_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    total_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    # Kept after the hold ends: it is the record of the deadline.
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    customer: Mapped[UserModel] = relationship(lazy="joined")
    venue: Mapped[VenueModel] = relationship(lazy="joined")
    bookings: Mapped[list[BookingGroupModel]] = relationship(
        order_by=BookingGroupModel.id, lazy="selectin"
    )


class RefundModel(Base):
    """A payment undone in full, at most once (ADR 0004 decision 7)."""

    __tablename__ = "refunds"

    id: Mapped[int] = mapped_column(primary_key=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    kind: Mapped[RefundKind] = mapped_column(refund_kind, nullable=False)
    amount_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
