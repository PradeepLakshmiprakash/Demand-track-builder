"""Escalations: anything missed escalates by default (flow-artifact §9).

Triggers (§9.1):
  not submitted   in an admin mail, still no GTD requisition ID at the next working day's mail time
  missing         sent to GTD, not in the DP sheet after the grace period   } opened by
  dropped         in the previous DP sheet, gone from the latest            } reconciliation
  incorrect       the DP sheet marks it "In Correct Demnad"                 }
  aging           no stage change for the account's aging days (linked demands)
  past start      requested start date has passed and there's no DOJ on or before it
  rejection limit panel rejections recorded in the app reach the account's limit (confirmed 24 Sep)
  panel SLA       no feedback within the account's panel hours of a scheduled interview's time

Ladder (§9.2): every escalation opens at L1 (the BU's LOB delivery head; demand owner and admins
copied), due after the account's L1 working days. Past due, the sweep promotes it to L2 (account
leadership), due after the L2 working days. A person closes it with a reason and an action.

`sweep` runs every two hours: open new escalations, promote overdue ones, then mail whoever hasn't
been told yet. Opening and mailing are separate (`notified_level`), so escalations opened by
reconciliation are mailed by the next sweep and a failed mail is retried.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core import mail
from app.core.config import get_settings
from app.core.enums import (
    FINISHED,
    DemandStatus,
    EscalationEventKind,
    EscalationStatus,
    EscalationType,
    InterviewOutcome,
    InterviewStatus,
    ResolutionAction,
    Role,
    StageOrigin,
)
from app.core.security import Actor
from app.core.workdays import add_working_days
from app.models import (
    Account,
    BusinessUnit,
    Candidate,
    Demand,
    Escalation,
    EscalationEvent,
    ExcelImport,
    ExcelRow,
    GtdSubmission,
    Interview,
    StageEvent,
    User,
    member_of,
)
from app.services.demand_service import record_stage

# Escalations about the GTD link itself; "resubmit" only makes sense for these.
LINK_TYPES = frozenset(
    {EscalationType.NOT_SUBMITTED, EscalationType.MISSING, EscalationType.DROPPED, EscalationType.INCORRECT}
)
# Stages where the DP sheet is driving progress, so a long silence means the demand is stuck.
AGING_STAGES = frozenset(
    {
        DemandStatus.LINKED,
        DemandStatus.COVERAGE_REQUIRED,
        DemandStatus.INTERVIEWING,
        DemandStatus.PANEL_SELECTED,
        DemandStatus.PROFILES_WITH_CLIENT,
        DemandStatus.OFFER_IN_PROCESS,
        DemandStatus.OFFER_IN_MARKET,
    }
)
# L1: GTD team admin and GTD admin team. L2: leadership, and the GTD team admin (confirmed 24 Sep).
RESOLVERS = {1: (Role.ADMIN, Role.ADMIN_TEAM), 2: (Role.LEADERSHIP, Role.ADMIN)}


class EscalationError(ValueError):
    pass


def _tz(account: Account) -> ZoneInfo:
    return ZoneInfo(account.settings.timezone)


def due_at(account: Account, opened: datetime, level: int = 1) -> datetime:
    """End of the account's working day, N working days after `opened` (L1 or L2 SLA)."""
    tz = _tz(account)
    days = account.l1_sla_days if level == 1 else account.l2_sla_days
    day = add_working_days(opened.astimezone(tz).date(), days)
    return datetime.combine(day, time(23, 59), tz)


def _event(
    db: Session,
    esc: Escalation,
    kind: EscalationEventKind,
    actor_id: int | None = None,
    note: str | None = None,
    at: datetime | None = None,
) -> None:
    db.add(
        EscalationEvent(
            escalation_id=esc.id,
            kind=kind.value,
            level=esc.level,
            actor_id=actor_id,
            note=note,
            at=at or datetime.now(UTC),
        )
    )


def open_escalation(
    db: Session, account: Account, demand: Demand, type_: EscalationType, detail: str, now: datetime
) -> Escalation | None:
    """Open an L1 escalation unless one of this type is already open for the demand (then None)."""
    existing = db.scalar(
        select(Escalation.id).where(
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
        notified_level=0,
    )
    db.add(esc)
    db.flush()
    _event(db, esc, EscalationEventKind.OPENED, note=detail, at=now)
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


# --- Facts the triggers read ------------------------------------------------------------------------


def last_stage_change(db: Session, demand_ids: list[int]) -> dict[int, datetime]:
    if not demand_ids:
        return {}
    rows = db.execute(
        select(StageEvent.demand_id, func.max(StageEvent.at))
        .where(StageEvent.demand_id.in_(demand_ids))
        .group_by(StageEvent.demand_id)
    ).all()
    return {demand_id: at for demand_id, at in rows}


def current_doj(db: Session, account_id: int) -> dict[int, date | None]:
    """DOJ per demand from the latest DP sheet, read from the row of the demand's current requisition."""
    latest = db.scalar(
        select(ExcelImport.id)
        .where(ExcelImport.account_id == account_id)
        .order_by(ExcelImport.imported_at.desc(), ExcelImport.id.desc())
        .limit(1)
    )
    if latest is None:
        return {}
    rows = db.execute(
        select(GtdSubmission.demand_id, ExcelRow.doj)
        .join(ExcelRow, ExcelRow.submission_id == GtdSubmission.id)
        .where(ExcelRow.import_id == latest)
    ).all()
    return {demand_id: doj for demand_id, doj in rows}


def notified_at(db: Session, demand_ids: list[int]) -> dict[int, datetime]:
    """When each demand last went out in an admin mail."""
    if not demand_ids:
        return {}
    rows = db.execute(
        select(StageEvent.demand_id, func.max(StageEvent.at))
        .where(StageEvent.demand_id.in_(demand_ids), StageEvent.to_stage == DemandStatus.NOTIFIED.value)
        .group_by(StageEvent.demand_id)
    ).all()
    return {demand_id: at for demand_id, at in rows}


def panel_rejections(db: Session, demand_ids: list[int]) -> dict[int, int]:
    if not demand_ids:
        return {}
    rows = db.execute(
        select(Interview.demand_id, func.count())
        .where(Interview.demand_id.in_(demand_ids), Interview.outcome == InterviewOutcome.REJECT.value)
        .group_by(Interview.demand_id)
    ).all()
    return {demand_id: n for demand_id, n in rows}


def overdue_panels(
    db: Session, account: Account, now: datetime
) -> dict[int, list[tuple[Interview, Candidate]]]:
    """Scheduled interviews with a known time whose feedback is past the panel SLA, by requisition."""
    cutoff = now - timedelta(hours=account.panel_timer_hours)
    rows = db.execute(
        select(Interview, Candidate)
        .join(Candidate, Candidate.id == Interview.candidate_id)
        .where(
            Candidate.account_id == account.id,
            Interview.demand_id.is_not(None),
            Interview.status == InterviewStatus.SCHEDULED.value,
            Interview.scheduled_at.is_not(None),
            Interview.scheduled_at < cutoff,
        )
        .order_by(Interview.scheduled_at)
    ).all()
    out: dict[int, list[tuple[Interview, Candidate]]] = {}
    for iv, c in rows:
        out.setdefault(iv.demand_id, []).append((iv, c))
    return out


def _past_start(d: Demand, doj: date | None, today: date) -> bool:
    return bool(
        d.start_date
        and d.start_date < today
        and d.status_enum not in FINISHED
        and d.status_enum is not DemandStatus.DRAFT
        and (doj is None or doj > d.start_date)
    )


def is_cleared(db: Session, account: Account, esc: Escalation, demand: Demand, now: datetime) -> bool:
    """Has the reason for this escalation gone away? Then "no further action" is allowed."""
    t = esc.type_enum
    s = demand.status_enum
    if t is EscalationType.NOT_SUBMITTED:
        return s not in (DemandStatus.SUBMITTED, DemandStatus.NOTIFIED)
    if t is EscalationType.MISSING:
        return s is not DemandStatus.MISSING
    if t is EscalationType.DROPPED:
        return s is not DemandStatus.DROPPED
    if t is EscalationType.INCORRECT:
        return s is not DemandStatus.INCORRECT
    if t is EscalationType.AGING:
        last = last_stage_change(db, [demand.id]).get(demand.id)
        return s not in AGING_STAGES or (last is not None and last > esc.opened_at)
    if t is EscalationType.PAST_START:
        today = now.astimezone(_tz(account)).date()
        return not _past_start(demand, current_doj(db, account.id).get(demand.id), today)
    if t is EscalationType.PANEL_SLA:
        return not overdue_panels(db, account, now).get(demand.id)
    return False  # rejection limit: a person decides what happens next


# --- The sweep --------------------------------------------------------------------------------------


@dataclass
class SweepResult:
    opened: list[str] = field(default_factory=list)
    promoted: list[str] = field(default_factory=list)
    mails: int = 0

    @property
    def message(self) -> str:
        return f"{len(self.opened)} opened, {len(self.promoted)} moved to L2, {self.mails} mails sent"


def open_triggered(db: Session, account: Account, now: datetime, result: SweepResult) -> None:
    tz = _tz(account)
    today = now.astimezone(tz).date()
    demands = list(
        db.scalars(
            select(Demand)
            .where(Demand.account_id == account.id, Demand.status.notin_([s.value for s in FINISHED]))
            .options(selectinload(Demand.submissions))
        )
    )
    ids = [d.id for d in demands]
    mailed = notified_at(db, ids)
    changed = last_stage_change(db, ids)
    doj = current_doj(db, account.id)
    rejected = panel_rejections(db, ids)
    overdue = overdue_panels(db, account, now)

    def raise_(d: Demand, t: EscalationType, detail: str) -> None:
        if open_escalation(db, account, d, t, detail, now) is not None:
            result.opened.append(f"{d.app_ref} {t.label.lower()}")

    for d in demands:
        s = d.status_enum
        # Not submitted: in a mail, no ID by the next working day's mail time.
        if s is DemandStatus.NOTIFIED and not d.submissions and d.id in mailed:
            sent = mailed[d.id].astimezone(tz)
            cutoff = datetime.combine(add_working_days(sent.date(), 1), account.mail_time, tz)
            if now >= cutoff:
                detail = f"In the admin mail of {sent:%d %b}; no GTD requisition ID by {cutoff:%d %b %H:%M}"
                raise_(d, EscalationType.NOT_SUBMITTED, detail)
        # Aging: linked demands with no stage change for the account's aging days.
        if s in AGING_STAGES and d.id in changed:
            quiet = (now - changed[d.id]).days
            if quiet >= account.aging_days:
                since = changed[d.id].astimezone(tz)
                raise_(
                    d, EscalationType.AGING, f"{s.label}, no stage change since {since:%d %b} ({quiet} days)"
                )
        # Rejection limit: too many panel rejections on one requisition.
        n = rejected.get(d.id, 0)
        if n >= account.rejection_limit:
            raise_(
                d,
                EscalationType.REJECTION_LIMIT,
                f"{n} candidates rejected by the panel (limit {account.rejection_limit})",
            )
        # Panel SLA: a scheduled interview with no feedback in time.
        panels = overdue.get(d.id)
        if panels:
            iv, c = panels[0]
            raise_(
                d,
                EscalationType.PANEL_SLA,
                f"{iv.round} for {c.name} on {iv.scheduled_at:%d %b}: no feedback after "
                f"{account.panel_timer_hours} h" + (f" (+{len(panels) - 1} more)" if len(panels) > 1 else ""),
            )
        # Past start date: no DOJ, or DOJ after the requested start.
        if _past_start(d, doj.get(d.id), today):
            late = (today - d.start_date).days  # type: ignore[operator]
            j = doj.get(d.id)
            when = f"DOJ {j:%d %b}, {(j - d.start_date).days} days after start" if j else "no DOJ"  # type: ignore[operator]
            detail = f"Start {d.start_date:%d %b} · {s.label.lower()} · {when} · {late} days late"
            raise_(d, EscalationType.PAST_START, detail)


def promote_overdue(db: Session, account: Account, now: datetime, result: SweepResult) -> None:
    overdue = db.scalars(
        select(Escalation)
        .join(Demand)
        .where(
            Demand.account_id == account.id,
            Escalation.status == EscalationStatus.OPEN.value,
            Escalation.level == 1,
            Escalation.due_at < now,
        )
        .options(selectinload(Escalation.events))
    )
    for esc in overdue:
        esc.level = 2
        esc.due_at = due_at(account, now, level=2)
        _event(db, esc, EscalationEventKind.PROMOTED, note="L1 due date passed", at=now)
        d = db.get_one(Demand, esc.demand_id)
        result.promoted.append(f"{d.app_ref} {esc.type_enum.label.lower()}")


def sweep(db: Session, account_id: int, now: datetime | None = None) -> SweepResult:
    now = now or datetime.now(UTC)
    account = db.get_one(Account, account_id)
    result = SweepResult()
    open_triggered(db, account, now, result)
    promote_overdue(db, account, now, result)
    db.commit()
    result.mails = notify_pending(db, account, now)
    return result


# --- Who hears about it ------------------------------------------------------------------------------


@dataclass
class Audience:
    to: list[str]
    cc: list[str]
    names: list[str]  # for the screen: "LOB delivery head (Asha P.), demand owner, admin"


def _users(db: Session, account_id: int, *roles: Role) -> list[User]:
    return list(db.scalars(select(User).where(member_of(account_id, *roles))))


def audience(db: Session, account: Account, esc: Escalation, demand: Demand) -> Audience:
    bu = db.get_one(BusinessUnit, demand.bu_id)
    owner = db.get_one(User, demand.owner_id)
    owners_cfg = account.settings.escalation_owners
    head = f"{owners_cfg.get('L1', 'LOB delivery head')}" + (
        f" ({bu.delivery_head_name})" if bu.delivery_head_name else ""
    )
    head_mail = [bu.delivery_head_email] if bu.delivery_head_email else []
    owner_mail = [owner.email] if owner.active else []
    admins = [u.email for u in _users(db, account.id, Role.ADMIN, Role.ADMIN_TEAM)]
    if esc.level == 1:
        to = head_mail or [u.email for u in _users(db, account.id, Role.ADMIN)]
        return Audience(to, sorted(set(owner_mail + admins) - set(to)), [head, "demand owner", "admin"])
    leaders = [u.email for u in _users(db, account.id, Role.LEADERSHIP)]
    to = leaders or [u.email for u in _users(db, account.id, Role.ADMIN)]
    return Audience(
        to,
        sorted(set(head_mail + owner_mail) - set(to)),
        [owners_cfg.get("L2", "Account leadership"), head, "demand owner"],
    )


def notify_pending(db: Session, account: Account, now: datetime) -> int:
    """Mail each group of recipients once about every escalation they haven't heard about yet."""
    pending = list(
        db.scalars(
            select(Escalation)
            .join(Demand)
            .where(
                Demand.account_id == account.id,
                Escalation.status == EscalationStatus.OPEN.value,
                Escalation.notified_level < Escalation.level,
            )
            .order_by(Escalation.level.desc(), Escalation.opened_at)
        )
    )
    groups: dict[tuple[int, tuple[str, ...]], list[tuple[Escalation, Demand, Audience]]] = defaultdict(list)
    for esc in pending:
        d = db.get_one(Demand, esc.demand_id)
        a = audience(db, account, esc, d)
        groups[(esc.level, tuple(sorted(a.to)))].append((esc, d, a))

    sent = 0
    for (level, to), items in groups.items():
        cc = sorted({c for _, _, a in items for c in a.cc} - set(to))
        mail.send(_compose(account, level, items, list(to), cc))
        for esc, _, a in items:
            esc.notified_level = esc.level
            _event(db, esc, EscalationEventKind.NOTIFIED, note="Mailed " + ", ".join(a.names), at=now)
        db.commit()  # per group, so a later failure doesn't re-send earlier mails
        sent += 1
    return sent


def _compose(
    account: Account,
    level: int,
    items: list[tuple[Escalation, Demand, Audience]],
    to: list[str],
    cc: list[str],
) -> mail.Mail:
    who = account.settings.escalation_owners.get(f"L{level}", f"L{level}")
    link = f"{get_settings().app_base_url}/escalations"
    plural = "s" if len(items) > 1 else ""
    subject = f"[Demand Tracker] {account.name} · {len(items)} escalation{plural} · L{level} {who}"
    tz = _tz(account)
    text = [f"{len(items)} escalation(s) need action at L{level} ({who}).", ""]
    rows = []
    for esc, d, _ in items:
        due = esc.due_at.astimezone(tz)
        text += [
            f"  {esc.type_enum.label} · {d.app_ref} {d.gtd_req_id or ''} {d.name}",
            f"    {esc.detail or ''}",
            f"    Due {due:%d %b}. Unresolved by then, it moves up.",
            "",
        ]
        rows.append(
            f"<tr><td style='padding:6px 10px'><b>{escape(esc.type_enum.label)}</b></td>"
            f"<td style='padding:6px 10px'><span style='font-family:monospace'>{escape(d.app_ref)}</span> "
            f"{escape(d.name)}<br><span style='color:#5E5A50'>{escape(esc.detail or '')}</span></td>"
            f"<td style='padding:6px 10px'>Due {due:%d %b}</td></tr>"
        )
    text += ["Resolve with a reason and an action:", link]
    html = (
        f"<p style='font:15px sans-serif'>{len(items)} escalation(s) need action at "
        f"<b>L{level} ({escape(who)})</b>.</p><table style='border-collapse:collapse;font:14px sans-serif'>"
        + "".join(rows)
        + f"</table><p style='font:14px sans-serif'><a href='{link}'>Open escalations</a></p>"
    )
    return mail.Mail(to=to, cc=cc, subject=subject, text="\n".join(text), html=html)


# --- Resolving ---------------------------------------------------------------------------------------


def can_resolve(actor: Actor, esc: Escalation) -> bool:
    """L1: GTD team admin and GTD admin team. L2: leadership and the GTD team admin."""
    return esc.status == EscalationStatus.OPEN.value and actor.role in RESOLVERS[esc.level]


def allowed_actions(db: Session, account: Account, esc: Escalation, demand: Demand) -> list[ResolutionAction]:
    actions = [ResolutionAction.EXTEND, ResolutionAction.CLOSE]
    if esc.type_enum in LINK_TYPES and demand.status_enum not in FINISHED:
        # Resubmit as it is (GTD lost it), or send back to the owner to correct it first.
        actions[0:0] = [ResolutionAction.RETURN, ResolutionAction.RESUBMIT]
    if is_cleared(db, account, esc, demand, datetime.now(UTC)):
        actions.append(ResolutionAction.NO_ACTION)
    return actions


def resolve(
    db: Session,
    actor: Actor,
    esc_id: int,
    *,
    reason: str,
    action: str,
    comment: str | None,
    extend_to: date | None = None,
    now: datetime | None = None,
) -> Escalation:
    now = now or datetime.now(UTC)
    esc = db.get(Escalation, esc_id)
    demand = db.get(Demand, esc.demand_id) if esc else None
    if esc is None or demand is None or demand.account_id != actor.account_id:
        raise EscalationError("Escalation not found.")
    if esc.status != EscalationStatus.OPEN.value:
        raise EscalationError("This escalation is already resolved.")
    if not can_resolve(actor, esc):
        who = "the GTD team admin or GTD admin team" if esc.level == 1 else "leadership or the GTD team admin"
        raise EscalationError(f"An L{esc.level} escalation is resolved by {who}.")
    account = db.get_one(Account, actor.account_id)
    if reason not in account.settings.resolution_reasons:
        raise EscalationError("Choose a reason.")
    try:
        act = ResolutionAction(action)
    except ValueError as e:
        raise EscalationError("Choose an action.") from e
    if act not in allowed_actions(db, account, esc, demand):
        raise EscalationError(f"'{act.label}' isn't available for this escalation.")
    comment = (comment or "").strip() or None
    note = f"{reason}" + (f": {comment}" if comment else "")

    if act is ResolutionAction.EXTEND:
        today = now.astimezone(_tz(account)).date()
        if extend_to is None or extend_to <= today:
            raise EscalationError("Pick a new due date after today.")
        esc.level = 1  # back to L1 with the new date (§9.2)
        esc.due_at = datetime.combine(extend_to, time(23, 59), _tz(account))
        esc.notified_level = min(esc.notified_level, 1)
        _event(db, esc, EscalationEventKind.EXTENDED, actor.id, f"Due {extend_to:%d %b %Y}. {note}", now)
        db.commit()
        return esc

    if act is ResolutionAction.RESUBMIT:
        # Back into the next admin mail; the new GTD ID will chain to the old one (gtd_service).
        record_stage(db, demand, DemandStatus.SUBMITTED, actor.id, StageOrigin.APP)
    elif act is ResolutionAction.RETURN:
        # The owner corrects and resubmits; it then goes into the admin mail as a resubmission.
        record_stage(db, demand, DemandStatus.RETURNED, actor.id, StageOrigin.APP)
        _mail_owner_returned(db, demand, reason, comment)
    elif act is ResolutionAction.CLOSE:
        record_stage(db, demand, DemandStatus.CLOSED, actor.id, StageOrigin.APP)
    _close(db, esc, actor.id, reason, act, comment, now)
    if act in (ResolutionAction.CLOSE, ResolutionAction.RESUBMIT, ResolutionAction.RETURN):
        # The demand's other link problems are settled by the same decision.
        for other in db.scalars(
            select(Escalation).where(
                Escalation.demand_id == demand.id,
                Escalation.status == EscalationStatus.OPEN.value,
                Escalation.id != esc.id,
            )
        ):
            if act is ResolutionAction.CLOSE or other.type_enum in LINK_TYPES:
                _close(
                    db, other, actor.id, reason, act, f"With {esc.type_enum.label.lower()} escalation", now
                )
    db.commit()
    return esc


def _mail_owner_returned(db: Session, demand: Demand, reason: str, comment: str | None) -> None:
    owner = db.get_one(User, demand.owner_id)
    if not owner.active:
        return
    link = f"{get_settings().app_base_url}/demands/{demand.app_ref}"
    text = (
        f"{demand.app_ref} ({demand.name}) was sent back to you for correction.\n"
        f"Reason: {reason}"
        + (f"\nWhat to fix: {comment}" if comment else "")
        + f"\n\nCorrect it and resubmit; it then goes back to the GTD admin team for GTD:\n{link}"
    )
    mail.send(
        mail.Mail(to=[owner.email], subject=f"[{demand.app_ref}] Please correct and resubmit", text=text)
    )


def _close(
    db: Session,
    esc: Escalation,
    actor_id: int,
    reason: str,
    act: ResolutionAction,
    comment: str | None,
    now: datetime,
) -> None:
    esc.status = EscalationStatus.RESOLVED.value
    esc.reason, esc.action, esc.comment = reason, act.value, comment
    esc.resolved_by, esc.resolved_at = actor_id, now
    _event(
        db,
        esc,
        EscalationEventKind.RESOLVED,
        actor_id,
        f"{act.label}. {reason}" + (f": {comment}" if comment else ""),
        now,
    )


def listing(
    db: Session, account_id: int, *, status: str = "open", level: int | None = None, type_: str | None = None
) -> list[tuple[Escalation, Demand]]:
    stmt = (
        select(Escalation, Demand)
        .join(Demand)
        .where(Demand.account_id == account_id)
        .options(
            selectinload(Demand.submissions), selectinload(Demand.owner), selectinload(Demand.business_unit)
        )
    )
    if status in (EscalationStatus.OPEN.value, EscalationStatus.RESOLVED.value):
        stmt = stmt.where(Escalation.status == status)
    if level in (1, 2):
        stmt = stmt.where(Escalation.level == level)
    if type_:
        stmt = stmt.where(Escalation.type == type_)
    order = (Escalation.due_at,) if status == "open" else (Escalation.resolved_at.desc(),)
    return [(e, d) for e, d in db.execute(stmt.order_by(*order, Escalation.id)).all()]
