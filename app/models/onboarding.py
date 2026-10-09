"""From offer to first billable day: the pre-joining checklist, and candidates who did not join."""

from datetime import date, datetime

from sqlalchemy import Boolean, CheckConstraint, Date, ForeignKey, Index, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base

ITEM_STATUSES = ("not_started", "in_progress", "done", "blocked", "not_needed")
OWNER_KINDS = ("owner", "person", "gtd_team", "outside")
EXIT_KINDS = {
    "declined": "Declined the offer",
    "dropped_out": "Accepted, then dropped out",
    "no_show": "Did not show on the joining day",
    "withdrawn": "We withdrew the offer",
}


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class OnboardingItem(Base):
    """One pre-joining item on one demand. Its due date is not stored: it is counted back from the
    joining date each time, so it moves when the joining date moves."""

    __tablename__ = "onboarding_items"
    __table_args__ = (
        CheckConstraint(_in("status", ITEM_STATUSES), name="status_valid"),
        CheckConstraint(_in("owner_kind", OWNER_KINDS), name="owner_kind_valid"),
        Index("ix_onboarding_items_demand", "demand_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    demand_id: Mapped[int] = mapped_column(ForeignKey("demands.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(160))
    owner_kind: Mapped[str] = mapped_column(String(10))
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # a person in the app, when known
    outside_label: Mapped[str | None] = mapped_column(String(80))  # the outside party, never a person
    due_days_before: Mapped[int] = mapped_column(SmallInteger)
    escalate: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(12), default="not_started")
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime | None]
    position: Mapped[int] = mapped_column(SmallInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class CandidateExit(Base):
    """A candidate who had an offer and did not join: what happened, why, and when."""

    __tablename__ = "candidate_exits"
    __table_args__ = (
        CheckConstraint(_in("kind", tuple(EXIT_KINDS)), name="kind_valid"),
        Index("ix_candidate_exits_account_on", "account_id", "on_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    demand_id: Mapped[int] = mapped_column(ForeignKey("demands.id"), index=True)
    candidate_id: Mapped[int | None] = mapped_column(ForeignKey("candidates.id"))
    candidate_name: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(12))
    reason: Mapped[str] = mapped_column(String(120))
    on_date: Mapped[date] = mapped_column(Date)
    recorded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
