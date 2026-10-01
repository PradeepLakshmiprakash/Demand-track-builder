"""Interview progress moves the demand; the BCM sheet doesn't pull it back while it lags.

The BCM sheet arrives about weekly, but panel feedback is recorded in the app the day it happens. So:

- A demand with a candidate in the panel (an interview recorded, scheduled or asked for, and not
  rejected) is **Interviewing**.
- A candidate selected at their last panel round, with no further round pending, makes it
  **Selected by panel** when the demand needs a client interview, or **Offer in process** when the
  panel's decision is final (`Demand.client_interview_required`, set when the demand is raised). Then
  the offer approval is raised and the demand owner is told.
- If every candidate is rejected, an app-set stage falls back to **Coverage required**.

The BCM sheet still wins when it's ahead or final (profiles with client, offers, staffed, cancelled,
incorrect). A sheet still saying Coverage required doesn't undo newer panel progress (`sheet_yields`).
Every change is a stage event, so interview progress also counts as progress for aging.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.config import get_settings
from app.core.enums import (
    APP_PROGRESS,
    PROGRESS_ORDER,
    DemandStatus,
    InterviewOutcome,
    InterviewStatus,
    StageOrigin,
)
from app.models import Account, Candidate, Demand, Interview, StageEvent, User
from app.services import margin_service
from app.services.demand_service import record_stage

PENDING = {InterviewStatus.REQUESTED.value, InterviewStatus.OPEN.value, InterviewStatus.SCHEDULED.value}
# Stages the app may change on interview evidence. Anything later belongs to the BCM sheet.
APPLIES_FROM = frozenset(
    {DemandStatus.SENT_TO_GTD, DemandStatus.LINKED, DemandStatus.COVERAGE_REQUIRED} | APP_PROGRESS
)


def _rank(s: DemandStatus) -> int:
    return PROGRESS_ORDER.index(s) if s in PROGRESS_ORDER else -1


@dataclass
class Evidence:
    stage: DemandStatus  # INTERVIEWING, PANEL_SELECTED or OFFER_IN_PROCESS
    note: str
    selected: Candidate | None = None
    selected_round: str | None = None


def evidence(db: Session, demand: Demand) -> Evidence | None:
    """What the panel records say about this demand, or None when there's nothing live."""
    cands = list(db.scalars(select(Candidate).where(Candidate.demand_id == demand.id)))
    if not cands:
        return None
    ivs = list(
        db.scalars(
            select(Interview).where(
                Interview.candidate_id.in_([c.id for c in cands]),
                Interview.status != InterviewStatus.DECLINED.value,
            )
        )
    )
    by_cand: dict[int, list[Interview]] = {}
    for iv in ivs:
        by_cand.setdefault(iv.candidate_id, []).append(iv)

    selected: list[tuple[Candidate, Interview]] = []
    alive: list[Candidate] = []
    for c in cands:
        mine = by_cand.get(c.id, [])
        done = [iv for iv in mine if iv.status == InterviewStatus.COMPLETED.value]
        pending = [iv for iv in mine if iv.status in PENDING]
        last = max(done, key=lambda iv: (iv.round_no, iv.submitted_at)) if done else None
        if last is not None and last.outcome == InterviewOutcome.REJECT.value and not pending:
            continue  # out of the running
        if last is not None and last.outcome == InterviewOutcome.SELECT.value and not pending:
            selected.append((c, last))
        elif last is not None or pending:
            alive.append(c)

    if selected:
        c, iv = max(selected, key=lambda x: x[1].submitted_at)  # type: ignore[arg-type, return-value]
        final = not demand.client_interview_required
        nxt = "offer next (panel decision is final)" if final else "client interview next"
        return Evidence(
            DemandStatus.OFFER_IN_PROCESS if final else DemandStatus.PANEL_SELECTED,
            f"{c.name} selected by the panel at {iv.round} · {nxt}",
            c,
            iv.round,
        )
    if alive:
        names = ", ".join(sorted(c.name for c in alive)[:3]) + (" …" if len(alive) > 3 else "")
        return Evidence(DemandStatus.INTERVIEWING, f"In the panel: {names}")
    return None


def apply(db: Session, account: Account, demand: Demand, actor_id: int | None) -> DemandStatus | None:
    """Move the demand to match its panel evidence. Returns the new stage when it changed."""
    cur = demand.status_enum
    if cur not in APPLIES_FROM:
        return None
    ev = evidence(db, demand)
    if ev is None:
        target = DemandStatus.COVERAGE_REQUIRED if cur in APP_PROGRESS else None
    elif cur in APP_PROGRESS or _rank(ev.stage) > _rank(cur):
        target = ev.stage
    else:
        target = None
    if target is None or target == cur:
        return None
    record_stage(db, demand, target, actor_id, StageOrigin.APP)
    if ev is not None and ev.selected is not None and target == ev.stage:
        if target is DemandStatus.OFFER_IN_PROCESS:
            margin_service.ensure_offer(db, account, demand, ev.selected.name, ev.selected.channel)
        _tell_owner(db, demand, ev)
    db.flush()
    return target


def apply_for(db: Session, demand_id: int | None, actor_id: int | None) -> None:
    if demand_id is None:
        return
    d = db.get(Demand, demand_id)
    if d is not None:
        apply(db, db.get_one(Account, d.account_id), d, actor_id)


def sheet_yields(db: Session, demand: Demand, sheet_stage: DemandStatus | None) -> bool:
    """True when the BCM sheet's stage is behind progress the app already recorded: panel progress,
    or an offer the owner marked as accepted. A sheet stage further on always wins."""
    cur = demand.status_enum
    if sheet_stage not in PROGRESS_ORDER or cur not in PROGRESS_ORDER or _rank(sheet_stage) >= _rank(cur):
        return False
    if cur in APP_PROGRESS:
        return True
    last = db.scalar(
        select(StageEvent.origin)
        .where(StageEvent.demand_id == demand.id)
        .order_by(StageEvent.at.desc(), StageEvent.id.desc())
        .limit(1)
    )
    return last == StageOrigin.APP.value


def notes(db: Session, demands: list[Demand]) -> dict[int, str]:
    """One-line panel progress for the demands list."""
    return {
        d.id: ev.note
        for d in demands
        if d.status_enum in APP_PROGRESS | {DemandStatus.COVERAGE_REQUIRED}
        and (ev := evidence(db, d)) is not None
    }


def _tell_owner(db: Session, demand: Demand, ev: Evidence) -> None:
    owner = db.get_one(User, demand.owner_id)
    if not owner.active or ev.selected is None:
        return
    req = demand.gtd_req_id or "no GTD ID"
    link = f"{get_settings().app_base_url}/demands/{demand.app_ref}"
    mail.send(
        mail.Mail(
            to=[owner.email],
            subject=f"[{req} | {demand.app_ref}] {ev.selected.name} selected by the panel",
            text=f"{ev.note}.\n{demand.name}\n\n{link}",
        )
    )
