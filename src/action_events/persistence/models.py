from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import ENUM, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.action_events.domain.enums import ActionEventType, ActorKind
from src.database.base import Base

# postgresql.ENUM with create_type=False — the migration owns creation.
action_event_type = ENUM(*ActionEventType, name="action_event_type", create_type=False)
actor_kind = ENUM(*ActorKind, name="actor_kind", create_type=False)


class ActionEventModel(Base):
    """One thing that happened to a booking or its payment. Append-only, and
    written in the transaction of the change it records (dbml Appendix A §17)."""

    __tablename__ = "action_events"
    __table_args__ = (
        CheckConstraint(
            "booking_group_id IS NOT NULL OR checkout_id IS NOT NULL",
            name="ck_action_event_has_subject",
        ),
        CheckConstraint(
            "(actor_kind = 'SYSTEM') = (actor_user_id IS NULL)", name="ck_action_event_actor"
        ),
        Index("ix_action_events_group_time", "booking_group_id", "occurred_at"),
        Index("ix_action_events_checkout_time", "checkout_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    booking_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("booking_groups.id", ondelete="CASCADE")
    )
    checkout_id: Mapped[int | None] = mapped_column(ForeignKey("checkouts.id", ondelete="CASCADE"))
    type: Mapped[ActionEventType] = mapped_column(action_event_type, nullable=False)
    actor_kind: Mapped[ActorKind] = mapped_column(actor_kind, nullable=False)
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
