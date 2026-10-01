from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

KINDS = {
    "access": "User access (add, change or remove a person)",
    "settings": "Account settings (lists, DP sheet format, thresholds, escalation rules)",
    "rate_card": "Rate card (vendor cost rates)",
    "other": "Something else",
}


class AdminRequest(Base):
    """Something the GTD team admin asks the Administrator to change in the app's controls.

    The Administrator doesn't decide what changes: they carry out requests, each raised here and
    mailed to them, and mark them done (which mails the requester back)."""

    __tablename__ = "admin_requests"
    __table_args__ = (
        CheckConstraint("kind IN ('access', 'settings', 'rate_card', 'other')", name="kind_valid"),
        CheckConstraint("status IN ('open', 'done', 'declined')", name="status_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    kind: Mapped[str] = mapped_column(String(12))
    details: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="open", server_default="open")
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    handled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    handled_at: Mapped[datetime | None]
    note: Mapped[str | None] = mapped_column(Text)
