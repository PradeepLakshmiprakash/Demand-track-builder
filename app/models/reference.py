"""Reference tables and day-by-day figures.

Practices and grades are rows, not free text: a demand's or a rate's practice and grade must exist here
for its account, and renaming one renames it everywhere at once (the foreign keys cascade the update).
The lists the Administrator edits in Account settings are mirrored into these tables by
app/core/reference.py; a name that comes in from a BCM sheet without being on the list is added here as
inactive, so nothing is lost and nothing is orphaned.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Practice(Base):
    __tablename__ = "practices"
    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_practices_account_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(Boolean, default=True)  # on the account's list today
    position: Mapped[int] = mapped_column(SmallInteger, default=0)


class Grade(Base):
    __tablename__ = "grades"
    __table_args__ = (UniqueConstraint("account_id", "name", name="uq_grades_account_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(SmallInteger, default=0)  # lowest grade first


class DailyFigure(Base):
    """The account's headline figures as they stood at the end of one day. Revenue lost and cost are
    worked out live from today's rates; these rows keep what was reported on the day."""

    __tablename__ = "daily_figures"
    __table_args__ = (UniqueConstraint("account_id", "day", name="uq_daily_figures_account_day"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    day: Mapped[date] = mapped_column(Date)
    open_positions: Mapped[int] = mapped_column(Integer)
    late_positions: Mapped[int] = mapped_column(Integer)
    revenue_lost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    nb_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    unbilled: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # joined, not yet billing
    escalations_open: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
