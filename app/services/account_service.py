"""Account settings: the only place client-specific rules change (plan.md principle 1)."""

from datetime import time
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.account_config import AccountConfig, StatusMapping, SupplyChannel
from app.models import Account, BusinessUnit, Demand, UserBusinessUnit


class SettingsError(ValueError):
    pass


class Thresholds(BaseModel):
    grace_days: int = Field(ge=0, le=60)
    l1_sla_days: int = Field(ge=1, le=60)
    l2_sla_days: int = Field(ge=1, le=60)
    panel_timer_hours: int = Field(ge=1, le=720)
    aging_days: int = Field(ge=1, le=365)
    rejection_limit: int = Field(ge=1, le=50)
    margin_threshold: Decimal = Field(ge=0, le=100)
    mail_time: time
    timezone: str


def get_account(db: Session, account_id: int) -> Account:
    return db.get_one(Account, account_id)


def _errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors())


def update_thresholds(db: Session, account_id: int, data: dict[str, str]) -> None:
    try:
        t = Thresholds.model_validate(data)
    except ValidationError as e:
        raise SettingsError(_errors(e)) from e
    try:
        ZoneInfo(t.timezone)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise SettingsError(f"Unknown time zone: {t.timezone}") from e

    acc = get_account(db, account_id)
    for k, v in t.model_dump(exclude={"timezone"}).items():
        setattr(acc, k, v)
    cfg = acc.settings
    cfg.timezone = t.timezone
    acc.settings = cfg
    db.commit()


def update_lists(db: Session, account_id: int, lists: dict[str, list[str]]) -> None:
    acc = get_account(db, account_id)
    cfg = acc.settings
    for key in ("practices", "grades", "regions", "work_modes", "categories", "resolution_reasons"):
        if key in lists:
            values = list(dict.fromkeys(v.strip() for v in lists[key] if v.strip()))
            if not values:
                raise SettingsError(f"{key.replace('_', ' ').capitalize()} can't be empty.")
            setattr(cfg, key, values)
    acc.settings = cfg
    db.commit()


def add_business_unit(db: Session, account_id: int, name: str) -> None:
    name = name.strip().upper()
    if not name:
        raise SettingsError("Business unit name is required.")
    exists = db.scalar(
        select(BusinessUnit.id).where(BusinessUnit.account_id == account_id, BusinessUnit.name == name)
    )
    if exists:
        raise SettingsError(f"{name} already exists.")
    db.add(BusinessUnit(account_id=account_id, name=name, active=True))
    db.commit()


def bu_usage(db: Session, bu_id: int) -> tuple[int, int]:
    demands = db.scalar(select(func.count(Demand.id)).where(Demand.bu_id == bu_id)) or 0
    users = db.scalar(select(func.count()).where(UserBusinessUnit.bu_id == bu_id)) or 0
    return demands, users


def update_business_unit(db: Session, account_id: int, bu_id: int, name: str, active: bool) -> None:
    bu = db.get(BusinessUnit, bu_id)
    if bu is None or bu.account_id != account_id:
        raise SettingsError("Business unit not found.")
    name = name.strip().upper()
    if not name:
        raise SettingsError("Business unit name is required.")
    clash = db.scalar(
        select(BusinessUnit.id).where(
            BusinessUnit.account_id == account_id, BusinessUnit.name == name, BusinessUnit.id != bu_id
        )
    )
    if clash:
        raise SettingsError(f"{name} already exists.")
    bu.name = name
    bu.active = active  # retired BUs keep their demands and history; they just can't be picked
    db.commit()


def update_status_mapping(db: Session, account_id: int, rows: list[dict[str, str]]) -> None:
    try:
        mapping = [StatusMapping.model_validate(r) for r in rows if r.get("status", "").strip()]
    except ValidationError as e:
        raise SettingsError(_errors(e)) from e
    seen: set[tuple[str, str]] = set()
    for m in mapping:
        key = (m.status_group.strip().casefold(), m.status.strip().casefold())
        if key in seen:
            raise SettingsError(f"'{m.status}' is mapped twice.")
        seen.add(key)
    acc = get_account(db, account_id)
    cfg = acc.settings
    cfg.status_mapping = mapping
    acc.settings = cfg
    db.commit()


def update_supply_channels(db: Session, account_id: int, rows: list[dict[str, str]]) -> None:
    try:
        channels = [
            SupplyChannel(
                key=r["label"].strip().lower().replace(" ", "_"),
                label=r["label"].strip(),
                sheet_marker=r.get("sheet_marker", "").strip(),
                needs_sourcing_req=r.get("needs_sourcing_req") == "on",
            )
            for r in rows
            if r.get("label", "").strip()
        ]
    except ValidationError as e:
        raise SettingsError(_errors(e)) from e
    acc = get_account(db, account_id)
    cfg: AccountConfig = acc.settings
    cfg.supply_channels = channels
    acc.settings = cfg
    db.commit()
