from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.enums import EscalationEventKind, EscalationStatus, EscalationType, ResolutionAction, check_in


class Escalation(Base):
    """Opened automatically by a trigger; closed only by a person with a reason and an action."""

    __tablename__ = "escalations"
    __table_args__ = (
        CheckConstraint(check_in("type", EscalationType), name="type_valid"),
        CheckConstraint(check_in("status", EscalationStatus), name="status_valid"),
        CheckConstraint("level IN (1, 2)", name="level_valid"),
        CheckConstraint(f"action IS NULL OR {check_in('action', ResolutionAction)}", name="action_valid"),
        CheckConstraint(
            "status = 'open' OR (reason IS NOT NULL AND action IS NOT NULL AND resolved_at IS NOT NULL)",
            name="resolved_needs_reason",
        ),
        Index("ix_escalations_status_due", "status", "due_at"),
        # At most one open escalation per demand and trigger type, so the sweep is idempotent.
        Index(
            "uq_escalations_open_per_type",
            "demand_id",
            "type",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    demand_id: Mapped[int] = mapped_column(ForeignKey("demands.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    level: Mapped[int] = mapped_column(SmallInteger, default=1)
    status: Mapped[str] = mapped_column(String(10), default=EscalationStatus.OPEN.value)
    detail: Mapped[str | None] = mapped_column(Text)
    opened_at: Mapped[datetime] = mapped_column(server_default=func.now())
    due_at: Mapped[datetime]
    reason: Mapped[str | None] = mapped_column(String(80))
    action: Mapped[str | None] = mapped_column(String(10))
    comment: Mapped[str | None] = mapped_column(Text)
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    resolved_at: Mapped[datetime | None]
    # Highest level whose owners have been mailed. The sweep mails whatever is above it.
    notified_level: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")

    events: Mapped[list["EscalationEvent"]] = relationship(
        order_by="EscalationEvent.id", cascade="all, delete-orphan"
    )

    @property
    def type_enum(self) -> EscalationType:
        return EscalationType(self.type)


class EscalationEvent(Base):
    """Audit trail: who and when for every open, mail, promotion, extension and resolution."""

    __tablename__ = "escalation_events"
    __table_args__ = (CheckConstraint(check_in("kind", EscalationEventKind), name="kind_valid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    escalation_id: Mapped[int] = mapped_column(ForeignKey("escalations.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))
    level: Mapped[int] = mapped_column(SmallInteger)
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    note: Mapped[str | None] = mapped_column(Text)
