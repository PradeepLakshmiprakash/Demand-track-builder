from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.enums import ApprovalRoute, Decision, check_in


class RateCard(Base):
    """Vendor cost rate. Dated rows keep historical margins correct."""

    __tablename__ = "rate_cards"
    __table_args__ = (
        CheckConstraint("effective_to IS NULL OR effective_to >= effective_from", name="dates_ordered"),
        Index("ix_rate_cards_lookup", "account_id", "grade", "practice", "region", "channel"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    grade: Mapped[str] = mapped_column(String(10))
    practice: Mapped[str | None] = mapped_column(String(40))  # null = any practice
    region: Mapped[str] = mapped_column(String(10))
    channel: Mapped[str] = mapped_column(String(40))
    cost_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)


class OfferApproval(Base):
    __tablename__ = "offer_approvals"
    __table_args__ = (
        CheckConstraint(check_in("route", ApprovalRoute), name="route_valid"),
        CheckConstraint(f"decision IS NULL OR {check_in('decision', Decision)}", name="decision_valid"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("candidates.id"), index=True)
    bill_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    cost_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    margin_pct: Mapped[Decimal] = mapped_column(Numeric(6, 2))
    route: Mapped[str] = mapped_column(String(12))
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str | None] = mapped_column(String(10))
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
