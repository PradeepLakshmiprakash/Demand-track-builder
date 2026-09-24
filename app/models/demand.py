from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Sequence,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.enums import DemandStatus, check_in
from app.models.account import BusinessUnit
from app.models.user import User

if TYPE_CHECKING:
    from app.models.gtd import GtdSubmission

# Postgres owns the app reference: DM-000142 comes from this sequence, never from Python.
demand_ref_seq = Sequence("demand_ref_seq", metadata=Base.metadata)
APP_REF_DEFAULT = text("'DM-' || lpad(nextval('demand_ref_seq')::text, 6, '0')")


class Demand(Base):
    """One client position. Bulk needs are N demands."""

    __tablename__ = "demands"
    __table_args__ = (
        CheckConstraint(check_in("status", DemandStatus), name="status_valid"),
        CheckConstraint("exp_min IS NULL OR exp_max IS NULL OR exp_min <= exp_max", name="exp_range"),
        CheckConstraint("client_rate IS NULL OR client_rate >= 0", name="rate_positive"),
        Index("ix_demands_account_status", "account_id", "status"),
        Index("ix_demands_owner", "owner_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    app_ref: Mapped[str] = mapped_column(String(12), unique=True, server_default=APP_REF_DEFAULT)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    bu_id: Mapped[int] = mapped_column(ForeignKey("business_units.id"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    practice: Mapped[str] = mapped_column(String(40))
    grade: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(20), default="Open")
    type: Mapped[str] = mapped_column(String(20), default="New")
    position_type: Mapped[str] = mapped_column(String(20), default="Billable")
    replaced_resource: Mapped[str | None] = mapped_column(String(120))
    primary_skills: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    secondary_skills: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    exp_min: Mapped[int | None] = mapped_column(Integer)
    exp_max: Mapped[int | None] = mapped_column(Integer)
    client_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # hourly bill rate
    start_date: Mapped[date | None] = mapped_column(Date)
    region: Mapped[str | None] = mapped_column(String(10))
    location: Mapped[str | None] = mapped_column(String(80))
    work_mode: Mapped[str | None] = mapped_column(String(20))
    hiring_manager: Mapped[str | None] = mapped_column(String(120))
    jd_path: Mapped[str | None] = mapped_column(Text)
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(24), default=DemandStatus.DRAFT.value)
    submitted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    business_unit: Mapped[BusinessUnit] = relationship()
    owner: Mapped[User] = relationship()
    submissions: Mapped[list["GtdSubmission"]] = relationship(
        back_populates="demand", order_by="GtdSubmission.submitted_at.desc()"
    )

    @property
    def status_enum(self) -> DemandStatus:
        return DemandStatus(self.status)

    @property
    def gtd_req_id(self) -> str | None:
        """The current requisition ID: the latest submission (resubmits chain to earlier ones)."""
        return self.submissions[0].gtd_req_id if self.submissions else None

    @property
    def gtd_name(self) -> str:
        """Name as entered on GTD. The prefix lets reconciliation match rows with no recorded ID."""
        return f"[{self.app_ref}] {self.name}"
