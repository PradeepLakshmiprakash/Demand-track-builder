"""Escalations: anything missed escalates by default (flow-artifact §9).

Phase 3 opens them from reconciliation (missing, dropped, incorrect demand). Phase 4 adds the sweep
for the remaining triggers, L1 → L2 promotion and the resolve screen, reusing `open_escalation`.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import EscalationStatus, EscalationType
from app.core.workdays import add_working_days
from app.models import Account, Demand, Escalation


def due_at(account: Account, opened: datetime, level: int = 1) -> datetime:
    """End of the account's working day, N working days after opening (L1 or L2 SLA)."""
    tz = ZoneInfo(account.settings.timezone)
    days = account.l1_sla_days if level == 1 else account.l2_sla_days
    day = add_working_days(opened.astimezone(tz).date(), days)
    return datetime.combine(day, time(23, 59), tz)


def open_escalation(
    db: Session, account: Account, demand: Demand, type_: EscalationType, detail: str, now: datetime
) -> Escalation | None:
    """Open an L1 escalation unless one of this type is already open for the demand (then None)."""
    existing = db.scalar(
        select(Escalation).where(
            Escalation.demand_id == demand.id,
            Escalation.type == type_.value,
            Escalation.status == EscalationStatus.OPEN.value,
        )
    )
    if existing is not None:
        return None
    esc = Escalation(
        demand_id=demand.id,
        type=type_.value,
        level=1,
        status=EscalationStatus.OPEN.value,
        detail=detail,
        opened_at=now,
        due_at=due_at(account, now),
    )
    db.add(esc)
    db.flush()
    return esc


def open_for(db: Session, demand_ids: list[int]) -> dict[int, list[Escalation]]:
    out: dict[int, list[Escalation]] = {}
    if demand_ids:
        for e in db.scalars(
            select(Escalation).where(
                Escalation.demand_id.in_(demand_ids), Escalation.status == EscalationStatus.OPEN.value
            )
        ):
            out.setdefault(e.demand_id, []).append(e)
    return out
