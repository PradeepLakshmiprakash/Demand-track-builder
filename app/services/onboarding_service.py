"""From offer to first billable day.

Three things the workflow didn't cover between "Offer made" and money coming in:

- the pre-joining checklist: the items a demand needs done before the candidate's first day, each with
  an owner and a due date counted back from the joining date;
- a candidate who had an offer and did not join: what happened and why, and the demand goes back to
  sourcing;
- the first billable day: joining and billing are not the same day, and the gap is counted.

What the items are, who owns them, the reasons lists and the waiting time before "joined, not billing"
escalates are the account's settings (AccountConfig.onboarding).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.account_config import ChecklistItem
from app.core.config import get_settings
from app.core.enums import Decision, DemandStatus, Role
from app.core.security import Actor
from app.core.workdays import add_working_days
from app.models import (
    Account,
    Candidate,
    CandidateExit,
    Demand,
    OfferApproval,
    OnboardingItem,
    User,
    member_of,
)
from app.models.onboarding import EXIT_KINDS
from app.services import demand_service
from app.services.escalation_service import current_doj
from app.services.loss_service import working_days

S = DemandStatus
OPEN_ITEM = ("not_started", "in_progress", "blocked")
STATUS_LABEL = {
    "not_started": "Not started",
    "in_progress": "In progress",
    "done": "Done",
    "blocked": "Blocked",
    "not_needed": "Not needed",
}
OFFER_STAGES = (S.OFFER_IN_PROCESS, S.OFFER_IN_MARKET)
GTD_ROLES = (Role.ADMIN, Role.ADMIN_TEAM)


class OnboardingError(ValueError):
    pass


# --- The checklist ----------------------------------------------------------------------------------


def items(db: Session, demand_id: int) -> list[OnboardingItem]:
    return list(
        db.scalars(
            select(OnboardingItem)
            .where(OnboardingItem.demand_id == demand_id)
            .order_by(OnboardingItem.position, OnboardingItem.id)
        )
    )


def ensure_checklist(db: Session, account: Account, demand: Demand) -> list[OnboardingItem]:
    """Give a demand its checklist once its offer is made. It keeps the items it was given: later
    changes to the account's list apply to demands whose offers are made after the change."""
    have = items(db, demand.id)
    if have or demand.status_enum is not S.OFFER_IN_MARKET:
        return have
    for n, c in enumerate(x for x in account.settings.onboarding.checklist if x.enabled):
        db.add(
            OnboardingItem(
                demand_id=demand.id,
                key=c.key,
                label=c.label,
                owner_kind=c.owner_kind,
                owner_id=demand.owner_id if c.owner_kind == "owner" else c.person_id,
                outside_label=c.outside_label if c.owner_kind == "outside" else None,
                due_days_before=c.due_days_before,
                escalate=c.escalate,
                status="not_started",
                position=n,
            )
        )
    db.flush()
    return items(db, demand.id)


def due_date(item: OnboardingItem, joining: date | None) -> date | None:
    return add_working_days(joining, -item.due_days_before) if joining else None


@dataclass
class ItemView:
    item: OnboardingItem
    who: str  # a person's name for someone in the app; the party for anyone outside it
    role: str  # what they are: "Demand owner", "GTD admin team", "outside the app"
    outside: bool
    due: date | None
    late_days: int  # working days past due while still open; 0 otherwise
    updated_by: str | None
    can_update: bool

    @property
    def label(self) -> str:
        return STATUS_LABEL[self.item.status]


@dataclass
class Checklist:
    rows: list[ItemView]
    joining: date | None
    days_to_joining: int | None

    @property
    def total(self) -> int:
        return sum(r.item.status != "not_needed" for r in self.rows)

    @property
    def done(self) -> int:
        return sum(r.item.status == "done" for r in self.rows)

    @property
    def overdue(self) -> int:
        return sum(r.late_days > 0 for r in self.rows)

    @property
    def blocked(self) -> int:
        return sum(r.item.status == "blocked" for r in self.rows)

    @property
    def percent(self) -> int:
        return round(self.done / self.total * 100) if self.total else 0


def can_update(actor: Actor, demand: Demand, item: OnboardingItem) -> bool:
    """The demand's owner keeps the whole checklist up to date, whoever each item is waiting on."""
    return actor.id == demand.owner_id


def checklist(db: Session, actor: Actor | None, demand: Demand, today: date) -> Checklist | None:
    have = items(db, demand.id)
    if not have:
        return None
    joining = current_doj(db, demand.account_id).get(demand.id)
    ids = {x for i in have for x in (i.owner_id, i.updated_by) if x is not None}
    names: dict[int, str] = {}
    if ids:
        names = {uid: name for uid, name in db.execute(select(User.id, User.name).where(User.id.in_(ids)))}
    rows = []
    for i in have:
        due = due_date(i, joining)
        late = working_days(due, today) if due and i.status in OPEN_ITEM and today > due else 0
        if i.owner_kind == "outside":
            who, role, outside = i.outside_label or "Outside party", "outside the app", True
        elif i.owner_id and i.owner_id in names:
            who = names[i.owner_id]
            role, outside = ("Demand owner" if i.owner_kind == "owner" else "GTD admin team"), False
            if i.owner_kind == "person":
                role = "Named for this item"
        else:  # the team's item, nobody has picked it up yet
            who, role, outside = "GTD admin team", "does it; the owner records it", False
        rows.append(
            ItemView(
                item=i,
                who=who,
                role=role,
                outside=outside,
                due=due,
                late_days=late,
                updated_by=names.get(i.updated_by) if i.updated_by else None,
                can_update=actor is not None and can_update(actor, demand, i) and i.status != "not_needed",
            )
        )
    return Checklist(
        rows=rows,
        joining=joining,
        days_to_joining=working_days(today, joining) if joining and joining > today else None,
    )


def update_item(db: Session, actor: Actor, demand: Demand, item_id: int, status: str, note: str) -> None:
    item = db.get(OnboardingItem, item_id)
    if item is None or item.demand_id != demand.id:
        raise OnboardingError("That checklist item isn't on this demand.")
    if not can_update(actor, demand, item) or item.status == "not_needed":
        raise OnboardingError("The checklist is kept up to date by the demand's owner.")
    if status not in ("not_started", "in_progress", "done", "blocked"):
        raise OnboardingError("Choose done, in progress, blocked or not started.")
    note = " ".join(note.split())[:300]
    if status == "blocked" and not note:
        raise OnboardingError("Say what the item is waiting on.")
    item.status, item.note = status, note or None
    item.updated_by, item.updated_at = actor.id, datetime.now(UTC)
    db.commit()


def close_open(db: Session, demand: Demand) -> int:
    """The candidate isn't joining after all: what was still open on the checklist is no longer needed."""
    n = 0
    for i in items(db, demand.id):
        if i.status in OPEN_ITEM:
            i.status, n = "not_needed", n + 1
    return n


def overdue_items(db: Session, account: Account, today: date) -> dict[int, list[tuple[OnboardingItem, date]]]:
    """Per demand with its offer made: the checklist items past their due date that escalate."""
    doj = current_doj(db, account.id)
    out: dict[int, list[tuple[OnboardingItem, date]]] = {}
    rows = db.execute(
        select(OnboardingItem, Demand.id)
        .join(Demand, Demand.id == OnboardingItem.demand_id)
        .where(
            Demand.account_id == account.id,
            Demand.status == S.OFFER_IN_MARKET.value,
            OnboardingItem.status.in_(OPEN_ITEM),
            OnboardingItem.escalate,
        )
        .order_by(OnboardingItem.position)
    )
    for item, demand_id in rows:
        due = due_date(item, doj.get(demand_id))
        if due and today > due:
            out.setdefault(demand_id, []).append((item, due))
    return out


def progress(db: Session, demand_ids: list[int]) -> dict[int, tuple[int, int]]:
    """Per demand: (items done, items that count)."""
    out: dict[int, tuple[int, int]] = {}
    if not demand_ids:
        return out
    for i in db.scalars(select(OnboardingItem).where(OnboardingItem.demand_id.in_(demand_ids))):
        if i.status == "not_needed":
            continue
        done, total = out.get(i.demand_id, (0, 0))
        out[i.demand_id] = (done + (i.status == "done"), total + 1)
    return out


# --- A candidate who did not join -------------------------------------------------------------------


def can_record_exit(actor: Actor, demand: Demand) -> bool:
    return demand.status_enum in OFFER_STAGES and (actor.id == demand.owner_id or actor.role in GTD_ROLES)


def exit_candidates(db: Session, demand: Demand) -> list[Candidate]:
    """Candidates on the demand who could be the one not joining: those with an offer first."""
    cands = list(
        db.scalars(select(Candidate).where(Candidate.demand_id == demand.id).order_by(Candidate.id.desc()))
    )
    offered = set(db.scalars(select(OfferApproval.candidate_id).where(OfferApproval.demand_id == demand.id)))
    return sorted(cands, key=lambda c: c.id not in offered)


def exits_for(db: Session, demand_id: int) -> list[CandidateExit]:
    return list(
        db.scalars(
            select(CandidateExit)
            .where(CandidateExit.demand_id == demand_id)
            .order_by(CandidateExit.id.desc())
        )
    )


def record_exit(
    db: Session, actor: Actor, demand: Demand, candidate_id: int | None, kind: str, reason: str, on: date
) -> CandidateExit:
    """The candidate will not join. The demand goes back to sourcing; what was pending for them closes."""
    if not can_record_exit(actor, demand):
        raise OnboardingError("Recorded by the demand's owner or the GTD admin team, while an offer is out.")
    account = db.get_one(Account, demand.account_id)
    cfg = account.settings.onboarding
    if kind not in EXIT_KINDS:
        raise OnboardingError("Choose what happened.")
    if reason not in cfg.exit_reasons:
        raise OnboardingError("Choose a reason from the list.")
    today = demand_service.account_today(db, account.id)
    if on > today:
        raise OnboardingError("The date can't be in the future.")
    cand = db.get(Candidate, candidate_id) if candidate_id else None
    if cand is not None and cand.demand_id != demand.id:
        raise OnboardingError("That candidate isn't on this demand.")
    if cand is None and candidate_id:
        raise OnboardingError("Choose the candidate.")
    out = CandidateExit(
        account_id=account.id,
        demand_id=demand.id,
        candidate_id=cand.id if cand else None,
        candidate_name=cand.name if cand else "Candidate not named",
        kind=kind,
        reason=reason,
        on_date=on,
        recorded_by=actor.id,
    )
    db.add(out)
    now = datetime.now(UTC)
    if cand is not None:
        cand.current_stage = "Did not join"
        for a in db.scalars(
            select(OfferApproval).where(
                OfferApproval.candidate_id == cand.id, OfferApproval.decision.is_(None)
            )
        ):
            if a.route is None:  # never priced: nothing to decide, so nothing to keep
                db.delete(a)
            else:
                a.decision, a.approver_id, a.decided_at = Decision.DECLINED.value, actor.id, now
                a.comment = (
                    f"Closed: the candidate did not join ({EXIT_KINDS[kind].lower()}; {reason.lower()})."
                )
    close_open(db, demand)
    demand.expected_doj = None
    demand_service.record_stage(db, demand, S.COVERAGE_REQUIRED, actor.id)
    db.flush()
    _mail_exit(db, account, demand, out, actor)
    db.commit()
    return out


def _mail_exit(db: Session, account: Account, demand: Demand, out: CandidateExit, actor: Actor) -> None:
    team = [u.email for u in db.scalars(select(User).where(member_of(account.id, *GTD_ROLES)))]
    owner = db.get_one(User, demand.owner_id)
    ref = demand.gtd_req_id or demand.app_ref
    mail.notify(
        mail.Mail(
            to=team,
            cc=[owner.email],
            subject=f"[{ref} | {demand.app_ref}] {out.candidate_name} will not join: back to sourcing",
            text=(
                f"{out.candidate_name} will not join {demand.name}.\n"
                f"What happened: {EXIT_KINDS[out.kind]} on {out.on_date:%d %b %Y}\n"
                f"Reason: {out.reason}\nRecorded by {actor.name}.\n\n"
                "The demand is back at Sourcing profiles. Its requested start date has not moved.\n"
                f"{get_settings().app_base_url}/demands/{demand.app_ref}"
            ),
        )
    )


# --- The first billable day -------------------------------------------------------------------------


@dataclass
class Billing:
    joined: date | None
    started: date | None  # the first billable day, once recorded
    waiting_days: int  # working days joined without billing (to the first billable day, or to today)
    amount: Decimal | None  # what those days are worth at the bill rate; None without a rate
    reason: str | None

    @property
    def waiting(self) -> bool:
        return self.started is None


def tracks_billing(demand: Demand) -> bool:
    """Billable positions that have joined. Proactive, non-billable ones use the costing box instead."""
    return demand.status_enum is S.STAFFED and demand.position_type == "Billable"


def joined_on(db: Session, account_id: int, demands: list[Demand]) -> dict[int, date]:
    """The day each joined demand's candidate started: the date of joining when known, otherwise the
    day the demand reached Joined."""
    doj = current_doj(db, account_id)
    reached = demand_service.finished_dates(db, demands)
    out: dict[int, date] = {}
    for d in demands:
        when = doj.get(d.id) or reached.get(d.id)
        if when:
            out[d.id] = when
    return out


def billing(db: Session, account: Account, demands: list[Demand], today: date) -> dict[int, Billing]:
    tracked = [d for d in demands if tracks_billing(d)]
    joined = joined_on(db, account.id, tracked)
    hours = Decimal(str(account.settings.billable_hours_per_day))
    out: dict[int, Billing] = {}
    for d in tracked:
        j = joined.get(d.id)
        until = d.billable_from or today
        days = working_days(j, until) if j and until > j else 0
        amount = Decimal(d.client_rate) * hours * days if d.client_rate is not None else None
        out[d.id] = Billing(
            joined=j, started=d.billable_from, waiting_days=days, amount=amount, reason=d.not_billing_reason
        )
    return out


def can_set_billing(actor: Actor, demand: Demand) -> bool:
    return tracks_billing(demand) and actor.id == demand.owner_id


def set_billing(db: Session, actor: Actor, demand: Demand, started: date | None, reason: str) -> None:
    """Record the first billable day, or why billing has not started yet."""
    if not can_set_billing(actor, demand):
        raise OnboardingError("Billing is confirmed by the demand's owner, once the candidate has joined.")
    account = db.get_one(Account, demand.account_id)
    today = demand_service.account_today(db, account.id)
    if started is not None:
        joined = joined_on(db, account.id, [demand]).get(demand.id)
        if started > today:
            raise OnboardingError("Record the first billable day on or after the day billing starts.")
        if joined and started < joined:
            raise OnboardingError(f"Billing can't start before the candidate joined ({joined:%d %b %Y}).")
        demand.billable_from, demand.not_billing_reason = started, None
        demand.billable_marked_at, demand.billable_marked_by = datetime.now(UTC), actor.id
    else:
        if reason not in account.settings.onboarding.not_billing_reasons:
            raise OnboardingError("Choose why billing has not started, or give the first billable day.")
        demand.billable_from, demand.not_billing_reason = None, reason
    db.commit()


def not_billing_due(db: Session, account: Account, today: date) -> dict[int, Billing]:
    """Joined, billable, no first billable day, and past the account's waiting time."""
    demands = list(
        db.scalars(select(Demand).where(Demand.account_id == account.id, Demand.status == S.STAFFED.value))
    )
    limit = account.settings.onboarding.not_billing_after_days
    return {
        i: b for i, b in billing(db, account, demands, today).items() if b.waiting and b.waiting_days >= limit
    }


def ask_for_confirmation(db: Session, account: Account, today: date) -> int:
    """On the joining day, ask the owner to confirm billing. Once per demand."""
    demands = [
        d
        for d in db.scalars(
            select(Demand).where(Demand.account_id == account.id, Demand.status == S.STAFFED.value)
        )
        if d.awaits_billing and d.billing_asked_at is None
    ]
    joined = joined_on(db, account.id, demands)
    n = 0
    for d in demands:
        day = joined.get(d.id)
        if day is None or day > today:
            continue
        owner = db.get_one(User, d.owner_id)
        team = [u.email for u in db.scalars(select(User).where(member_of(account.id, Role.ADMIN)))]
        ref = d.gtd_req_id or d.app_ref
        sent = mail.notify(
            mail.Mail(
                to=[owner.email],
                cc=team,
                subject=f"[{ref} | {d.app_ref}] Joined on {day:%d %b}: confirm the first billable day",
                text=(
                    f"The candidate for {d.name} joined on {day:%d %b %Y}.\n\n"
                    "Confirm the first billable day on the demand. It is normally the joining day; if "
                    "billing starts later, give that day or the reason it is waiting.\n"
                    "Until you confirm, the position counts as open and revenue lost keeps counting.\n\n"
                    f"{get_settings().app_base_url}/demands/{d.app_ref}#billing"
                ),
            )
        )
        if sent:
            d.billing_asked_at = datetime.now(UTC)
            n += 1
    db.flush()
    return n


# --- Settings ---------------------------------------------------------------------------------------


def _key(label: str, taken: set[str]) -> str:
    base = "".join(ch if ch.isalnum() else "_" for ch in label.lower()).strip("_")[:32] or "item"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


def parse_checklist(
    rows: list[dict[str, str]], people: set[int], old: list[ChecklistItem]
) -> list[ChecklistItem]:
    """The settings form's rows as checklist items. A row with no label is dropped."""
    by_key = {c.key: c for c in old}
    out: list[ChecklistItem] = []
    taken: set[str] = set()
    for r in rows:
        label = " ".join((r.get("label") or "").split())[:160]
        if not label:
            continue
        kind = r.get("owner_kind") or "owner"
        if kind not in ("owner", "person", "gtd_team", "outside"):
            raise OnboardingError(f"{label}: choose who owns it.")
        person = int(r["person_id"]) if (r.get("person_id") or "").isdigit() else None
        if kind == "person" and person not in people:
            raise OnboardingError(f"{label}: choose the person who owns it.")
        outside = " ".join((r.get("outside_label") or "").split())[:80]
        if kind == "outside" and not outside:
            raise OnboardingError(f"{label}: name the outside party (for example GTD staffing).")
        days = r.get("due_days_before") or ""
        if not days.isdigit() or int(days) > 90:
            raise OnboardingError(f"{label}: due is a number of working days before joining, 0 to 90.")
        key = r.get("key") or ""
        key = key if key in by_key and key not in taken else _key(label, taken | set(by_key))
        taken.add(key)
        out.append(
            ChecklistItem(
                key=key,
                label=label,
                owner_kind=kind,  # type: ignore[arg-type]
                person_id=person if kind == "person" else None,
                outside_label=outside if kind == "outside" else None,
                due_days_before=int(days),
                escalate=r.get("escalate") == "1",
                enabled=r.get("enabled") == "1",
            )
        )
    if len(out) > 30:
        raise OnboardingError("30 checklist items at most.")
    return out
