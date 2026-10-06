"""The audit trail: who changed what, from what to what, and when.

One row per change to a record that matters (a demand, a person's access, the account's settings, a
rate, a business unit, an interviewer profile). Rows are written by app/core/audit.py as part of the
same database transaction as the change itself, so a change and its record can't come apart. Rows are
never edited or deleted by the app.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditEntry(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_account_at", "account_id", "at"),
        Index("ix_audit_log_entity", "entity", "entity_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_name: Mapped[str] = mapped_column(String(120))  # kept as text: the trail outlives the person
    entity: Mapped[str] = mapped_column(String(40))  # demand, user, access, account, rate, …
    entity_id: Mapped[int | None]
    entity_label: Mapped[str] = mapped_column(String(200))  # how a person would name it: DM-000142, Priya N.
    action: Mapped[str] = mapped_column(String(10))  # created, changed, deleted
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)  # field → [before, after]
    at: Mapped[datetime] = mapped_column(server_default=func.now())
