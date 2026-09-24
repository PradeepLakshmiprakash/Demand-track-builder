"""Demands as each actor is allowed to see them.

`visible_demands` is the single scope filter every demand query goes through (screens, JSON API and
later the jobs), driven by the actor's visibility scope, account and BUs.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import Select, or_, select
from sqlalchemy.orm import Session, selectinload

from app.core import storage
from app.core.account_config import AccountConfig
from app.core.enums import (
    BEFORE_GTD,
    FINISHED,
    LINK_PROBLEMS,
    DemandStatus,
    EscalationStatus,
    Role,
    Scope,
    StageOrigin,
)
from app.core.security import Actor
from app.models import Account, BusinessUnit, Demand, Escalation, Interview, StageEvent
from app.schemas.raise_demand import DemandForm


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
        DemandStatus.RETURNED: "Sent back to you for correction: fix it and resubmit",
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
    attention = bool(escs) or past_start or s in LINK_PROBLEMS or s is DemandStatus.RETURNED
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

    from app.services.margin_service import notes as offer_notes  # margin_service imports this module too
    from app.services.pipeline_service import notes as panel_notes  # pipeline_service imports this module

    progress = panel_notes(db, demands) | offer_notes(db, demands)
    rows = []
    for d in demands:
        label, chip, note, attention = _describe(d, escs.get(d.id, []), today)
        if d.id in progress and not note:
            note = progress[d.id]
        elif d.id in progress:
            note = f"{note} · {progress[d.id]}"
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


# --- Intake: raise, edit, submit (Phase 2) ---------------------------------------------------------

# Locked once it's in the admin mail; a demand sent back for correction opens up again.
EDITABLE = frozenset({DemandStatus.DRAFT, DemandStatus.SUBMITTED, DemandStatus.RETURNED})


class DemandError(ValueError):
    pass


def get_visible(db: Session, actor: Actor, app_ref: str) -> Demand | None:
    """A demand the actor may see, or None (callers answer 404, never 403, so refs don't leak)."""
    return db.scalar(
        visible_demands(actor)
        .where(Demand.app_ref == app_ref)
        .options(
            selectinload(Demand.submissions), selectinload(Demand.business_unit), selectinload(Demand.owner)
        )
    )


def can_change(actor: Actor, demand: Demand) -> bool:
    return can_edit(actor, demand) and demand.status_enum in EDITABLE


def record_stage(
    db: Session,
    demand: Demand,
    to: DemandStatus,
    actor_id: int | None,
    origin: StageOrigin = StageOrigin.APP,
    import_id: int | None = None,
) -> bool:
    """Every status change goes through here so it is written as a stage event. True if it changed."""
    before = demand.status
    if before == to.value:
        return False
    demand.status = to.value
    db.add(
        StageEvent(
            demand_id=demand.id,
            from_stage=before,
            to_stage=to.value,
            origin=origin.value,
            actor_id=actor_id,
            import_id=import_id,
        )
    )
    return True


def _resolve_bu(db: Session, actor: Actor, form: DemandForm) -> int:
    if actor.role is Role.DEMAND_OWNER:
        if len(actor.bu_ids) != 1:
            raise DemandError("Your user has no single business unit. Ask the admin to fix your access.")
        return next(iter(actor.bu_ids))  # a demand owner raises for their own BU only
    bu = db.get(BusinessUnit, form.bu_id) if form.bu_id else None
    if bu is None or bu.account_id != actor.account_id or not bu.active:
        raise DemandError("Choose a business unit.")
    return bu.id


def _check(cfg: AccountConfig, form: DemandForm, *, submit: bool, today: date) -> None:
    problems = []
    for label, value, allowed in (
        ("Practice", form.practice, cfg.practices),
        ("Grade", form.grade, cfg.grades),
        ("Category", form.category, cfg.categories),
        ("Region", form.region, cfg.regions),
        ("Work mode", form.work_mode, cfg.work_modes),
    ):
        if value is not None and value not in allowed:
            problems.append(f"{label} '{value}' isn't in the account's list")
    if submit:
        missing = form.missing_for_submit()
        if missing:
            problems.append("Needed before submitting: " + ", ".join(missing))
        if form.start_date and form.start_date < today:
            problems.append("Requested start date is in the past")
    if problems:
        raise DemandError(". ".join(problems) + ".")


def _apply(demand: Demand, form: DemandForm) -> None:
    for field in (
        "name", "practice", "grade", "category", "type", "replaced_resource", "position_type",
        "client_interview_required",
        "primary_skills", "secondary_skills", "exp_min", "exp_max", "start_date", "region",
        "location", "work_mode", "hiring_manager",
    ):  # fmt: skip
        setattr(demand, field, getattr(form, field))
    if form.client_rate is not None:  # blank keeps the rate on file (demand owners can't see it)
        demand.client_rate = form.client_rate


Upload = tuple[str, bytes]  # (file name, content) of a job description


def _check_jd(jd: Upload | None) -> None:
    if jd is not None:
        try:
            storage.check(jd[0], jd[1], storage.JD_EXTENSIONS)
        except storage.StorageError as e:
            raise DemandError(f"Job description not saved: {e}.") from e


def _store_jd(demand: Demand, jd: Upload | None) -> None:
    if jd is not None:
        demand.jd_path = storage.save(f"jd/{demand.app_ref}", jd[0], jd[1], storage.JD_EXTENSIONS)


def create_demands(
    db: Session, actor: Actor, form: DemandForm, *, submit: bool, jd: Upload | None = None
) -> list[Demand]:
    """Save (draft) or submit. One demand per position: N positions make N demands with their own refs."""
    account = db.get_one(Account, actor.account_id)
    today = account_today(db, actor.account_id)
    bu_id = _resolve_bu(db, actor, form)
    _check(account.settings, form, submit=submit, today=today)
    _check_jd(jd)
    created = []
    for _ in range(form.positions):
        d = Demand(
            account_id=actor.account_id, bu_id=bu_id, owner_id=actor.id, status=DemandStatus.DRAFT.value
        )
        _apply(d, form)
        db.add(d)
        db.flush()  # Postgres assigns app_ref here
        _store_jd(d, jd)
        db.add(StageEvent(demand_id=d.id, from_stage=None, to_stage=DemandStatus.DRAFT.value,
                          origin=StageOrigin.APP.value, actor_id=actor.id))  # fmt: skip
        if submit:
            _submit(db, actor, d)
        created.append(d)
    db.commit()
    return created


def update_demand(
    db: Session, actor: Actor, demand: Demand, form: DemandForm, *, submit: bool, jd: Upload | None = None
) -> Demand:
    if not can_change(actor, demand):
        raise DemandError("This demand can't be changed any more: it's already with the admin.")
    _check_jd(jd)
    account = db.get_one(Account, actor.account_id)
    _check(account.settings, form, submit=submit or demand.status_enum is DemandStatus.SUBMITTED,
           today=account_today(db, actor.account_id))  # fmt: skip
    if actor.role is not Role.DEMAND_OWNER:
        demand.bu_id = _resolve_bu(db, actor, form)
    _apply(demand, form)
    _store_jd(demand, jd)
    if submit:
        _submit(db, actor, demand)
    db.commit()
    return demand


def _submit(db: Session, actor: Actor, demand: Demand) -> None:
    if demand.status_enum in (DemandStatus.DRAFT, DemandStatus.RETURNED):
        record_stage(db, demand, DemandStatus.SUBMITTED, actor.id)
        demand.submitted_at = datetime.now(UTC)


def stage_history(db: Session, demand: Demand) -> list[StageEvent]:
    return list(
        db.scalars(
            select(StageEvent).where(StageEvent.demand_id == demand.id).order_by(StageEvent.at, StageEvent.id)
        )
    )
