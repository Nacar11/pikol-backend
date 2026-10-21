from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.bookings.domain.enums import BookingStatus, BookingType, PaymentMethod, PaymentStatus
from src.courts.persistence.models import CourtModel
from src.database.base import Base
from src.users.persistence.models import UserModel

SLOT_CONSTRAINT = "uq_court_slot_active"
REFERENCE_CONSTRAINT = "booking_groups_reference_code_key"

# postgresql.ENUM with create_type=False — the migration owns creation (see
# ParameterModel.value_type for why it is not sqlalchemy.Enum).
booking_type = ENUM(*BookingType, name="booking_type", create_type=False)
booking_status = ENUM(*BookingStatus, name="booking_status", create_type=False)
payment_status = ENUM(*PaymentStatus, name="payment_status", create_type=False)
payment_method = ENUM(*PaymentMethod, name="payment_method", create_type=False)


class BookingGroupModel(Base):
    """The unit: one payment, one cancellation (spec §4). A group is exactly
    one court; a multi-hour session is N slot rows under it."""

    __tablename__ = "booking_groups"
    __table_args__ = (
        Index("ix_booking_groups_player_id_booking_status", "player_id", "booking_status"),
        Index("ix_booking_groups_court_id_booking_status", "court_id", "booking_status"),
        Index("ix_booking_groups_booking_status_expires_at", "booking_status", "expires_at"),
        # dbml Appendix A §5–8.
        CheckConstraint(
            "price_centavos >= 0 AND fee_centavos >= 0 AND total_centavos >= 0",
            name="ck_group_money_non_negative",
        ),
        CheckConstraint(
            "(booking_status = 'PENDING') = (expires_at IS NOT NULL)",
            name="ck_pending_has_expiry",
        ),
        CheckConstraint(
            "cancelled_at IS NULL OR "
            "(cancelled_by_user_id IS NOT NULL AND cancellation_reason IS NOT NULL)",
            name="ck_cancellation_is_documented",
        ),
        CheckConstraint(
            "(type = 'PLAYER_BOOKING') = (player_id IS NOT NULL)",
            name="ck_player_only_on_player_booking",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reference_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    court_id: Mapped[int] = mapped_column(
        ForeignKey("courts.id", ondelete="RESTRICT"), nullable=False
    )
    player_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    type: Mapped[BookingType] = mapped_column(booking_type, nullable=False)
    booking_status: Mapped[BookingStatus] = mapped_column(booking_status, nullable=False)
    payment_status: Mapped[PaymentStatus] = mapped_column(payment_status, nullable=False)
    # Frozen at creation. Never recomputed from price_rules.
    price_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    fee_centavos: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    total_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_method: Mapped[PaymentMethod | None] = mapped_column(payment_method)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    needs_resolution: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false(), index=True
    )
    notes: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    court: Mapped[CourtModel] = relationship(lazy="joined")
    player: Mapped[UserModel | None] = relationship(foreign_keys=[player_id], lazy="joined")
    slots: Mapped[list["BookingSlotModel"]] = relationship(
        order_by="BookingSlotModel.starts_at", lazy="selectin", cascade="all, delete-orphan"
    )


class BookingSlotModel(Base):
    """One court-hour. Carries copies of the group's status and court_id
    because a Postgres index predicate cannot reference another table (spec
    §4.2) — and only BookingRepository writes either."""

    __tablename__ = "booking_slots"
    __table_args__ = (
        # THE constraint (dbml Appendix A §2, ADR 0001). PARTIAL: only
        # CANCELLED and EXPIRED release a slot, so COMPLETED and NO_SHOW keep
        # blocking it.
        Index(
            SLOT_CONSTRAINT,
            "court_id",
            "starts_at",
            unique=True,
            postgresql_where=text("booking_status NOT IN ('CANCELLED', 'EXPIRED')"),
        ),
        CheckConstraint(
            "EXTRACT(MINUTE FROM starts_at) = 0 AND EXTRACT(SECOND FROM starts_at) = 0",
            name="ck_slot_on_the_hour",
        ),
        CheckConstraint("price_centavos >= 0", name="ck_slot_price_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_group_id: Mapped[int] = mapped_column(
        ForeignKey("booking_groups.id", ondelete="CASCADE"), nullable=False, index=True
    )
    court_id: Mapped[int] = mapped_column(
        ForeignKey("courts.id", ondelete="RESTRICT"), nullable=False
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    price_centavos: Mapped[int] = mapped_column(Integer, nullable=False)
    booking_status: Mapped[BookingStatus] = mapped_column(booking_status, nullable=False)
