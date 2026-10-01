"""Account settings: the only place client-specific rules change (plan.md principle 1)."""

import re
from datetime import time
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.account_config import (
    DP_FIELD_LABELS,
    DP_FIELDS,
    AccountConfig,
    EscalationRule,
    StatusMapping,
    SupplyChannel,
)
from app.core.enums import EscalationType, Severity
from app.models import Account, BusinessUnit, Demand, UserBusinessUnit


class SettingsError(ValueError):
    pass


class Thresholds(BaseModel):
    grace_days: int = Field(ge=0, le=60)
    l1_sla_days: int = Field(2, ge=1, le=60)  # superseded by response days per severity
    l2_sla_days: int = Field(3, ge=1, le=60)
    panel_timer_hours: int = Field(ge=1, le=720)
    aging_days: int = Field(ge=1, le=365)
    rejection_limit: int = Field(ge=1, le=50)
    margin_threshold: Decimal = Field(ge=0, le=100)
    mail_time: time
    timezone: str
    billable_hours_per_day: float = Field(8.0, gt=0, le=24)


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
    for k, v in t.model_dump(exclude={"timezone", "billable_hours_per_day"}).items():
        setattr(acc, k, v)
    cfg = acc.settings
    cfg.timezone = t.timezone
    cfg.billable_hours_per_day = t.billable_hours_per_day
    acc.settings = cfg
    db.commit()


def update_lists(db: Session, account_id: int, lists: dict[str, list[str]]) -> None:
    acc = get_account(db, account_id)
    cfg = acc.settings
    keys = (
        "practices",
        "grades",
        "regions",
        "work_modes",
        "categories",
        "resolution_reasons",
        "interview_ratings",
    )
    for key in keys:
        if key in lists:
            values = list(dict.fromkeys(v.strip() for v in lists[key] if v.strip()))
            if not values:
                raise SettingsError(f"{key.replace('_', ' ').capitalize()} can't be empty.")
            setattr(cfg, key, values)
    for level in ("L1", "L2"):
        label = " ".join(lists.get(f"escalation_owner_{level.lower()}", [])).strip()
        if label:
            cfg.escalation_owners[level] = label[:80]
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


def update_business_unit(
    db: Session,
    account_id: int,
    bu_id: int,
    name: str,
    active: bool,
    head_name: str = "",
    head_email: str = "",
) -> None:
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
    head_email = head_email.strip()
    if head_email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", head_email):
        raise SettingsError(f"'{head_email}' isn't an email address.")
    bu.name = name
    bu.active = active  # retired BUs keep their demands and history; they just can't be picked
    bu.delivery_head_name = head_name.strip() or None
    bu.delivery_head_email = head_email or None
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


def update_dp_columns(db: Session, account_id: int, headers: dict[str, str], blanks: list[str]) -> None:
    """Which DP sheet header holds each field. Required fields can't be left blank."""
    columns = {}
    for f, (default, required) in DP_FIELDS.items():
        value = " ".join((headers.get(f) or "").split())
        if not value and required:
            raise SettingsError(f"{DP_FIELD_LABELS[f]} needs a column name.")
        columns[f] = value or default
    names = [v.casefold() for v in columns.values()]
    dupes = sorted({v for v in names if names.count(v) > 1})
    if dupes:
        raise SettingsError("Each column can hold only one field: " + ", ".join(dupes) + ".")
    acc = get_account(db, account_id)
    cfg = acc.settings
    cfg.dp_columns = columns
    cfg.dp_blank_values = list(dict.fromkeys(b.strip() for b in blanks if b.strip()))
    acc.settings = cfg
    db.commit()


def update_escalation_rules(db: Session, account_id: int, form: dict[str, str]) -> None:
    """Per trigger: on/off, who is responsible, severity and the steps the email spells out; plus the
    working days to respond per severity and who is informed when one goes overdue."""
    acc = get_account(db, account_id)
    cfg = acc.settings
    rules = {}
    for t in EscalationType:
        steps = " ".join((form.get(f"steps_{t.value}") or "").split())
        if not steps:
            raise SettingsError(f"{t.label}: say what the responsible person should do.")
        try:
            rules[t.value] = EscalationRule(
                enabled=form.get(f"enabled_{t.value}") == "on",
                responsible=form.get(f"responsible_{t.value}", ""),  # type: ignore[arg-type]
                severity=form.get(f"severity_{t.value}", ""),  # type: ignore[arg-type]
                steps=steps[:400],
            )
        except ValidationError as e:
            raise SettingsError(f"{t.label}: {_errors(e)}") from e
    days = {}
    for sev in Severity:
        raw = (form.get(f"days_{sev.value}") or "").strip()
        if not raw.isdigit() or not 1 <= int(raw) <= 30:
            raise SettingsError(f"{sev.label} severity: days to respond must be 1 to 30.")
        days[sev.value] = int(raw)
    if not days["high"] <= days["medium"] <= days["low"]:
        raise SettingsError("Higher severity can't have more days to respond than a lower one.")
    cfg.escalation_rules = rules
    cfg.response_days = days
    cfg.l2_inform_leadership = form.get("inform_leadership") == "on"
    cfg.l2_inform_delivery_head = form.get("inform_delivery_head") == "on"
    acc.settings = cfg
    db.commit()
