from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
        CheckConstraint(
            "status = 'draft' OR (practice IS NOT NULL AND grade IS NOT NULL)", name="submitted_is_complete"
        ),
        Index("ix_demands_account_status", "account_id", "status"),
        # A demand's practice and grade exist for its account; a rename there carries to here.
        ForeignKeyConstraint(
            ["account_id", "practice"],
            ["practices.account_id", "practices.name"],
            name="fk_demands_practice",
            onupdate="CASCADE",
        ),
        ForeignKeyConstraint(
            ["account_id", "grade"],
            ["grades.account_id", "grades.name"],
            name="fk_demands_grade",
            onupdate="CASCADE",
        ),
        Index("ix_demands_owner", "owner_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    app_ref: Mapped[str] = mapped_column(String(12), unique=True, server_default=APP_REF_DEFAULT)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    bu_id: Mapped[int] = mapped_column(ForeignKey("business_units.id"))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(String(200))
    # Optional while a draft; required to submit (DemandForm.missing_for_submit).
    practice: Mapped[str | None] = mapped_column(String(40))
    grade: Mapped[str | None] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(20), default="Open")
    type: Mapped[str] = mapped_column(String(20), default="New")
    position_type: Mapped[str] = mapped_column(String(20), default="Billable")
    # Known when the demand is raised: does a panel select go to a client interview, or is it final?
    client_interview_required: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    replaced_resource: Mapped[str | None] = mapped_column(String(120))
    # Replacement only: the last working day of the person being replaced.
    lwd: Mapped[date | None] = mapped_column(Date)
    # Proactive, non-billable only: the day the client started billing (the owner records it). Until
    # then the position costs the account from its start date (costing_service).
    billable_from: Mapped[date | None] = mapped_column(Date)
    billable_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    billable_marked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    not_billing_reason: Mapped[str | None] = mapped_column(String(120))  # joined, why billing waits
    # The earlier demand for the same position, when that one was abandoned and this one re-raises it.
    replaces_demand_id: Mapped[int | None] = mapped_column(ForeignKey("demands.id"))
    primary_skills: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    secondary_skills: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    exp_min: Mapped[int | None] = mapped_column(Integer)
    exp_max: Mapped[int | None] = mapped_column(Integer)
    client_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))  # hourly bill rate
    start_date: Mapped[date | None] = mapped_column(Date)
    # Entered by the demand owner once the offer is accepted. The BCM sheet's DOJ overrides it.
    expected_doj: Mapped[date | None] = mapped_column(Date)
    # When the owner last set it: it stands until a BCM sheet imported after that gives another date.
    expected_doj_at: Mapped[datetime | None]
    region: Mapped[str | None] = mapped_column(String(10))
    location: Mapped[str | None] = mapped_column(String(80))
    work_mode: Mapped[str | None] = mapped_column(String(20))
    hiring_manager: Mapped[str | None] = mapped_column(String(120))
    jd_path: Mapped[str | None] = mapped_column(Text)  # an uploaded file, on demands raised before 9 Oct 2026
    jd_text: Mapped[str | None] = mapped_column(Text)  # the job description, typed on the demand form
    billing_asked_at: Mapped[datetime | None]  # when the owner was asked to confirm the first billable day
    custom_fields: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(24), default=DemandStatus.DRAFT.value)
    submitted_at: Mapped[datetime | None]
    # When interviewers sharing a technology were told about this requisition (once).
    interviewers_alerted_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    business_unit: Mapped[BusinessUnit] = relationship()
    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
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
        """Name as entered on GTD: the plain demand name. The GTD admin team doesn't type the DM
        reference there; the demand is tied to GTD by the requisition ID they link back."""
        return self.name

    @property
    def is_proactive_nb(self) -> bool:
        """A Proactive position the client isn't paying for: it costs the account, and loses no revenue."""
        return (self.category or "").casefold() == "proactive" and self.position_type == "Non-billable"

    @property
    def awaits_billing(self) -> bool:
        """Joined, billable, and its owner hasn't confirmed the first billable day yet. Such a position
        is still in client onboarding: it counts as open, and revenue is still being lost on it."""
        return (
            self.status == DemandStatus.STAFFED.value
            and self.position_type == "Billable"
            and not self.is_proactive_nb
            and self.billable_from is None
        )

    @property
    def loss_from(self) -> date | None:
        """The first day the position is unfilled and losing revenue: the requested start date, or
        the day after the leaver's last working day when that is later (they bill until they go)."""
        if self.start_date is None or self.lwd is None:
            return self.start_date
        return max(self.start_date, self.lwd + timedelta(days=1))

    @property
    def uncovered_days(self) -> int | None:
        """Replacement: calendar days between the leaver's last working day and the requested start."""
        if self.start_date is None or self.lwd is None or self.start_date <= self.lwd:
            return None
        return (self.start_date - self.lwd).days - 1
