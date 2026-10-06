from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import ApprovalRoute, Decision, check_in


class RateCard(Base):
    """Vendor cost rate per hour. Dated rows keep historical margins correct: a rate is never edited,
    a new row takes over from its start date and the previous one is closed the day before."""

    __tablename__ = "rate_cards"
    __table_args__ = (
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="dates_ordered"),
        CheckConstraint("cost_rate >= 0", name="cost_positive"),
        Index("ix_rate_cards_lookup", "account_id", "grade", "practice"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    grade: Mapped[str] = mapped_column(String(10))
    practice: Mapped[str | None] = mapped_column(String(40))  # null = any practice (older cards only)
    cost_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class OfferApproval(Base):
    """Margin check for an offer (flow-artifact §8). Priced from the client bill rate and the dated rate
    card; routed to the lead admin at or above the account's margin cut-off, else leadership.
    Until both rates are known it waits unpriced (no route) with the reason in `blocked_reason`."""

    __tablename__ = "offer_approvals"
    __table_args__ = (
        CheckConstraint(f"route IS NULL OR {check_in('route', ApprovalRoute)}", name="route_valid"),
        CheckConstraint(f"decision IS NULL OR {check_in('decision', Decision)}", name="decision_valid"),
        CheckConstraint("(route IS NULL) = (margin_pct IS NULL)", name="priced_means_routed"),
        CheckConstraint(
            "decision IS NULL OR (route IS NOT NULL AND approver_id IS NOT NULL AND decided_at IS NOT NULL)",
            name="decided_needs_approver",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    demand_id: Mapped[int] = mapped_column(ForeignKey("demands.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    channel: Mapped[str | None] = mapped_column(String(40))
    bill_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    cost_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    margin_pct: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    route: Mapped[str | None] = mapped_column(String(12))
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    priced_on: Mapped[date | None] = mapped_column(Date)  # rate card date used
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str | None] = mapped_column(String(10))
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
