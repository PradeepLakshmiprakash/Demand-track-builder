from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

if TYPE_CHECKING:
    from app.models.demand import Demand


class NotificationBatch(Base):
    """One row per daily admin mail: which demands it listed."""

    __tablename__ = "notification_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    sent_at: Mapped[datetime] = mapped_column(server_default=func.now())
    demand_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)


class GtdSubmission(Base):
    """Link between an app demand and a GTD requisition ID. The ID can never be linked twice."""

    __tablename__ = "gtd_submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    demand_id: Mapped[int] = mapped_column(ForeignKey("demands.id"), index=True)
    gtd_req_id: Mapped[str] = mapped_column(String(20), unique=True)
    submitted_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    submitted_at: Mapped[datetime] = mapped_column(server_default=func.now())
    previous_submission_id: Mapped[int | None] = mapped_column(ForeignKey("gtd_submissions.id"))

    demand: Mapped["Demand"] = relationship(back_populates="submissions")
