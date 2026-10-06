from datetime import datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Time,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.account_config import AccountConfig
from app.core.db import Base


class Account(Base):
    """A client. Holds every client-specific rule: thresholds as columns, lists and mappings in config."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    grace_days: Mapped[int] = mapped_column(Integer, default=3)
    l1_sla_days: Mapped[int] = mapped_column(Integer, default=2)
    l2_sla_days: Mapped[int] = mapped_column(Integer, default=3)
    panel_timer_hours: Mapped[int] = mapped_column(Integer, default=48)
    aging_days: Mapped[int] = mapped_column(Integer, default=14)
    rejection_limit: Mapped[int] = mapped_column(Integer, default=3)
    margin_threshold: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("30.00"))
    mail_time: Mapped[time] = mapped_column(Time, default=time(9, 0))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    business_units: Mapped[list["BusinessUnit"]] = relationship(
        back_populates="account", order_by="BusinessUnit.id"
    )

    @property
    def settings(self) -> AccountConfig:
        return AccountConfig.model_validate(self.config or {})

    @settings.setter
    def settings(self, value: AccountConfig) -> None:
        self.config = value.model_dump(mode="json")


class BusinessUnit(Base):
    __tablename__ = "business_units"
    __table_args__ = (
        UniqueConstraint("account_id", "name", name="uq_business_units_account_name"),
        CheckConstraint("nb_cap IS NULL OR nb_cap >= 0", name="nb_cap_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    name: Mapped[str] = mapped_column(String(60))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # L1 escalation owner for this BU. Not an app user: they're mailed, they don't sign in.
    delivery_head_name: Mapped[str | None] = mapped_column(String(120))
    delivery_head_email: Mapped[str | None] = mapped_column(String(254))
    # Agreed number of non-billable (proactive) positions for this BU; set by the lead admin.
    nb_cap: Mapped[int | None] = mapped_column(Integer)

    account: Mapped[Account] = relationship(back_populates="business_units")
