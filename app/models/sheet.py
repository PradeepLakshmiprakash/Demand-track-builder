from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import RowOutcome, StageOrigin, check_in


class ExcelImport(Base):
    """One uploaded BCM sheet. Rows are snapshots, never overwritten, so imports can be compared."""

    __tablename__ = "excel_imports"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    file_path: Mapped[str] = mapped_column(Text)
    file_name: Mapped[str] = mapped_column(String(255))
    sheet_date: Mapped[date | None] = mapped_column(Date)
    file_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    uploaded_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    imported_at: Mapped[datetime] = mapped_column(server_default=func.now())
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    # Counts and warnings from the last reconciliation run (see reconcile_service.Summary).
    summary: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, server_default=text("'{}'::jsonb"))


class ExcelRow(Base):
    """One BCM sheet row as it was in that import. Personal data (originator, candidates) stays here."""

    __tablename__ = "excel_rows"
    __table_args__ = (
        CheckConstraint("match_tier IS NULL OR match_tier BETWEEN 1 AND 3", name="tier_valid"),
        CheckConstraint(check_in("outcome", RowOutcome), name="outcome_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("excel_imports.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    gtd_req_id: Mapped[str | None] = mapped_column(String(20), index=True)
    demand_request_name: Mapped[str | None] = mapped_column(Text)
    originator: Mapped[str | None] = mapped_column(String(160))
    practice: Mapped[str | None] = mapped_column(String(40))
    grade: Mapped[str | None] = mapped_column(String(10))
    region: Mapped[str | None] = mapped_column(String(10))
    start_date: Mapped[date | None] = mapped_column(Date)
    gettalent_req_id: Mapped[str | None] = mapped_column(String(40))
    candidate_details: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(String(80))
    status_group: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str | None] = mapped_column(String(120))
    candidate_name: Mapped[str | None] = mapped_column(String(160))
    doj: Mapped[date | None] = mapped_column(Date)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    submission_id: Mapped[int | None] = mapped_column(ForeignKey("gtd_submissions.id"))
    match_tier: Mapped[int | None] = mapped_column(SmallInteger)  # 1 exact, 2 name prefix, 3 fuzzy/manual
    outcome: Mapped[str] = mapped_column(
        String(12), default=RowOutcome.UNMATCHED.value, server_default=RowOutcome.UNMATCHED.value
    )
    # Fuzzy candidates: [{"demand_id", "app_ref", "score", "reasons"}], best first.
    suggestions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, default=list, server_default=text("'[]'::jsonb")
    )
    note: Mapped[str | None] = mapped_column(Text)
    matched_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class StageEvent(Base):
    """Every stage change, timestamped. Feeds aging, SLA timers and escalations."""

    __tablename__ = "stage_events"
    __table_args__ = (
        CheckConstraint(check_in("origin", StageOrigin), name="origin_valid"),
        Index("ix_stage_events_demand_at", "demand_id", "at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    demand_id: Mapped[int] = mapped_column(ForeignKey("demands.id"))
    from_stage: Mapped[str | None] = mapped_column(String(24))
    to_stage: Mapped[str] = mapped_column(String(24))
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    origin: Mapped[str] = mapped_column(String(10))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    import_id: Mapped[int | None] = mapped_column(ForeignKey("excel_imports.id"))
