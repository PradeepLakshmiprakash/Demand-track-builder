from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import CandidateSource, InterviewOutcome, InterviewSource, InterviewStatus, check_in


class Candidate(Base):
    """One person on one requisition. Staffing schedules interviews outside the app, so a candidate can
    exist before anyone knows their requisition: `demand_id` stays empty until they're mapped."""

    __tablename__ = "candidates"
    __table_args__ = (
        CheckConstraint(check_in("source", CandidateSource), name="source_valid"),
        # The same person appears once per requisition.
        Index(
            "uq_candidates_demand_name",
            "demand_id",
            func.lower(text("name")),
            unique=True,
            postgresql_where=text("demand_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    demand_id: Mapped[int | None] = mapped_column(ForeignKey("demands.id"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    channel: Mapped[str | None] = mapped_column(String(40))
    current_stage: Mapped[str | None] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(
        String(10), default=CandidateSource.SHEET.value, server_default="sheet"
    )
    cv_path: Mapped[str | None] = mapped_column(Text)
    # The client's interview result ("select" or "reject"), recorded by the demand owner: the client
    # has no access to the app or the BCM sheet.
    client_outcome: Mapped[str | None] = mapped_column(String(10))
    client_decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    client_decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    client_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Interview(Base):
    """One interview round for a candidate, or a placeholder for one.

    Staffing schedules interviews outside the app, so any part can be unknown at first: the
    requisition (until the candidate is mapped), the interviewer (L2 has no owner yet) and the time.
    A panelist's recommendation is recorded on it; an L2 starts as a request the demand owner approves.
    """

    __tablename__ = "interviews"
    __table_args__ = (
        CheckConstraint(f"outcome IS NULL OR {check_in('outcome', InterviewOutcome)}", name="outcome_valid"),
        CheckConstraint(check_in("status", InterviewStatus), name="status_valid"),
        CheckConstraint(check_in("source", InterviewSource), name="source_valid"),
        CheckConstraint(
            "status <> 'completed' OR (outcome IS NOT NULL AND submitted_at IS NOT NULL)",
            name="completed_has_outcome",
        ),
        CheckConstraint(
            "status <> 'scheduled' OR interviewer_id IS NOT NULL", name="scheduled_has_interviewer"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    demand_id: Mapped[int | None] = mapped_column(ForeignKey("demands.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    round: Mapped[str] = mapped_column(String(10))  # L1, L2
    status: Mapped[str] = mapped_column(
        String(12), default=InterviewStatus.SCHEDULED.value, server_default="scheduled"
    )
    source: Mapped[str] = mapped_column(String(10), default=InterviewSource.APP.value, server_default="app")
    interviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    scheduled_at: Mapped[datetime | None]
    # Single-use link for the interviewer's feedback; issued when an interviewer is assigned.
    feedback_token: Mapped[str | None] = mapped_column(String(64), unique=True)
    ratings: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    outcome: Mapped[str | None] = mapped_column(String(10))
    comments: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None]
    # The panelist can ask for another round in their feedback.
    needs_next_round: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # An L2 request: who asked, why, and the demand owner's decision.
    requested_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    request_note: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None]
    decision_note: Mapped[str | None] = mapped_column(Text)
    # From an external interview platform (Karat): its id and report link.
    external_ref: Mapped[str | None] = mapped_column(String(80))
    report_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def status_enum(self) -> InterviewStatus:
        return InterviewStatus(self.status)

    @property
    def round_no(self) -> int:
        return int(self.round[1:]) if self.round[:1] == "L" and self.round[1:].isdigit() else 0
