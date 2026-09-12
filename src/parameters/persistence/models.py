from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import ENUM
from sqlalchemy.orm import Mapped, mapped_column

from src.database.base import Base


class ParameterModel(Base):
    """One tunable business rule (spec §4.5).

    Values are stored as text and coerced by the Pydantic model in
    src/parameters/domain/. `value_type` is not used for coercion — it exists
    so the admin UI can render the right input control.
    """

    __tablename__ = "parameters"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    # postgresql.ENUM, not the generic sqlalchemy.Enum: `create_type` is a
    # PG-dialect parameter, and the generic type SWALLOWS it silently — the
    # object ends up with no create_type at all and keeps default
    # create-with-table behaviour. Any metadata.create_all() or autogenerate
    # then emits CREATE TYPE a second time and fails with "type already
    # exists", which is the exact failure this is meant to prevent.
    value_type: Mapped[str] = mapped_column(
        ENUM(
            "int",
            "decimal",
            "bool",
            "string",
            name="parameter_value_type",
            create_type=False,  # the migration owns creation
        ),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # No FK yet — the users table arrives in Phase 2, which adds the
    # constraint in its own migration.
    updated_by_user_id: Mapped[int | None] = mapped_column(nullable=True)
