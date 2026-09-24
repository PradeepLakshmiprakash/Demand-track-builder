from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import InterviewOutcome, check_in


class Candidate(Base):
    """One person on one requisition."""

    __tablename__ = "candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    demand_id: Mapped[int] = mapped_column(ForeignKey("demands.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    channel: Mapped[str | None] = mapped_column(String(40))
    current_stage: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Interview(Base):
    """Created by the scheduler already tied to the requisition, so the interviewer never needs the ID."""

    __tablename__ = "interviews"
    __table_args__ = (
        CheckConstraint(f"outcome IS NULL OR {check_in('outcome', InterviewOutcome)}", name="outcome_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    demand_id: Mapped[int] = mapped_column(ForeignKey("demands.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"))
    round: Mapped[str] = mapped_column(String(10))  # L1, L2, Client
    interviewer_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scheduled_at: Mapped[datetime | None]
    feedback_token: Mapped[str] = mapped_column(String(64), unique=True)
    ratings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    outcome: Mapped[str | None] = mapped_column(String(10))
    comments: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def round_no(self) -> int:
        return int(self.round[1:]) if self.round[:1] == "L" and self.round[1:].isdigit() else 0
