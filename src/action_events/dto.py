from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from src.action_events.domain.enums import ActionEventType, ActorKind

# Never stored: they have no write of their own (design, "Derived on read").
DerivedType = Literal["PLAY_STARTED", "HOLD_ENDED"]


class EventActor(BaseModel):
    kind: ActorKind
    name: str | None


class ActionEventOut(BaseModel):
    id: int | None  # None when derived
    type: ActionEventType | DerivedType
    occurred_at: datetime
    actor: EventActor
    booking_id: int | None  # None for a checkout-level payment event
    reference_code: str | None
    court_name: str | None
    data: dict[str, Any]
    derived: bool
