"""Demands as each actor is allowed to see them.

`visible_demands` is the single scope filter every demand query goes through (screens, JSON API and
later the jobs), driven by the actor's visibility scope, account and BUs.
"""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import (
    BEFORE_GTD,
    FINISHED,
    LINK_PROBLEMS,
    DemandStatus,
    EscalationStatus,
    Role,
    Scope,
)
from app.core.security import Actor
from app.models import Account, Demand, Escalation, Interview


def visible_demands(actor: Actor) -> Select[tuple[Demand]]:
    stmt = select(Demand).where(Demand.account_id == actor.account_id)
    match actor.scope:
        case Scope.FULL:
            return stmt
        case Scope.OWN:
            return stmt.where(Demand.owner_id == actor.id)
        case Scope.OWN_BU_READ:
            return stmt.where(or_(Demand.owner_id == actor.id, Demand.bu_id.in_(actor.bu_ids)))
        case Scope.ASSIGNED_INTERVIEWS:
            assigned = select(Interview.demand_id).where(Interview.interviewer_id == actor.id)
            return stmt.where(Demand.id.in_(assigned))
    raise AssertionError(actor.scope)


def can_edit(actor: Actor, demand: Demand) -> bool:
    """Visibility is wider than edit rights: BU read-only viewers and leadership can't change a demand."""
    return demand.account_id == actor.account_id and (demand.owner_id == actor.id or actor.role is Role.ADMIN)


def account_today(db: Session, account_id: int) -> date:
    tz = db.get(Account, account_id).settings.timezone  # type: ignore[union-attr]
    return datetime.now(ZoneInfo(tz)).date()


FILTERS = {
    "all": "All",
    "attention": "Needs attention",
    "before_gtd": "Before GTD",
    "linked": "Linked to GTD",
    "finished": "Staffed or closed",
}


@dataclass
class DemandRow:
    demand: Demand
    req_id: str | None
    status_label: str
    chip: str
    note: str
    attention: bool
    read_only: bool
    open_escalations: list[Escalation]

    @property
    def group(self) -> str:
        s = self.demand.status_enum
        if s in FINISHED:
            return "finished"
        return "before_gtd" if s in BEFORE_GTD else "linked"


def _describe(d: Demand, escs: list[Escalation], today: date) -> tuple[str, str, str, bool]:
    """Status chip text, colour, the one-line note under the title, and whether it needs attention."""
    s = d.status_enum
    label, chip = s.label, s.chip
    past_start = bool(d.start_date and d.start_date < today and s not in FINISHED)
    notes = {
        DemandStatus.DRAFT: "Not submitted yet",
        DemandStatus.SUBMITTED: "Goes out in the next admin mail",
        DemandStatus.NOTIFIED: "In the admin mail, waiting for GTD entry",
        DemandStatus.SENT_TO_GTD: "On GTD, waiting for the DP sheet",
        DemandStatus.MISSING: "Not in the DP sheet after the grace period",
        DemandStatus.DROPPED: "Was in the DP sheet, gone from the latest one",
        DemandStatus.INCORRECT: "DP sheet marks it as an incorrect demand",
    }
    note = notes.get(s, "")
    if escs:
        top = max(escs, key=lambda e: (e.level, e.opened_at))
        label, chip = f"{top.type_enum.short} · escalated L{top.level}", "esc"
        note = note or (top.detail or "")
    elif past_start and chip != "esc":
        chip = "risk"
    if past_start:
        late = (today - d.start_date).days  # type: ignore[operator]
        note = f"{late} days past start date" + (f" · {note}" if note else "")
    attention = bool(escs) or past_start or s in LINK_PROBLEMS
    return label, chip, note, attention


def demand_rows(db: Session, actor: Actor, today: date | None = None) -> list[DemandRow]:
    """Every demand the actor may see, described for the list screens."""
    today = today or account_today(db, actor.account_id)
    stmt = visible_demands(actor).options(
        selectinload(Demand.submissions), selectinload(Demand.business_unit), selectinload(Demand.owner)
    )
    demands = list(db.scalars(stmt.order_by(Demand.app_ref.desc())))

    escs: dict[int, list[Escalation]] = {}
    if demands:
        for e in db.scalars(
            select(Escalation).where(
                Escalation.demand_id.in_([d.id for d in demands]),
                Escalation.status == EscalationStatus.OPEN.value,
            )
        ):
            escs.setdefault(e.demand_id, []).append(e)

    rows = []
    for d in demands:
        label, chip, note, attention = _describe(d, escs.get(d.id, []), today)
        rows.append(
            DemandRow(
                demand=d,
                req_id=d.gtd_req_id,
                status_label=label,
                chip=chip,
                note=note,
                attention=attention,
                read_only=not can_edit(actor, d),
                open_escalations=escs.get(d.id, []),
            )
        )
    return rows


def filter_rows(rows: list[DemandRow], filter_key: str = "all", bu_id: int | None = None) -> list[DemandRow]:
    if bu_id:
        rows = [r for r in rows if r.demand.bu_id == bu_id]
    if filter_key == "attention":
        return [r for r in rows if r.attention]
    if filter_key in ("before_gtd", "linked", "finished"):
        return [r for r in rows if r.group == filter_key]
    return rows


def filter_counts(rows: list[DemandRow]) -> dict[str, int]:
    return {key: len(filter_rows(rows, key)) for key in FILTERS}


def summary(rows: list[DemandRow]) -> dict[str, int]:
    """The four cards above the list. Counts open demands only."""
    open_rows = [r for r in rows if r.group != "finished"]
    return {
        "open": len(open_rows),
        "before_gtd": sum(r.group == "before_gtd" for r in open_rows),
        "in_coverage": sum(r.group == "linked" and not r.attention for r in open_rows),
        "attention": sum(r.attention for r in open_rows),
    }
