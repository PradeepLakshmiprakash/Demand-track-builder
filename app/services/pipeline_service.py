"""Interview progress moves the demand; the BCM sheet doesn't pull it back while it lags.

The BCM sheet arrives about weekly, but panel feedback is recorded in the app the day it happens. So:

- A demand with a candidate in the panel (an interview recorded, scheduled or asked for, and not
  rejected) is **Interviewing**.
- A candidate selected at their last panel round, with no further round pending, makes it
  **Selected by panel** when the demand needs a client interview, or **Offer in process** when the
  panel's decision is final (`Demand.client_interview_required`, set when the demand is raised). Then
  the offer approval is raised and the demand owner is told.
- If every candidate is rejected, an app-set stage falls back to **Coverage required**.
- The client has no access to anything, so the **client interview result** is recorded by the demand
  owner (`record_client_decision`) or arrives with the BCM sheet. Selected: the offer approval is
  raised. Not selected: the candidate is out and the demand goes back to wherever its other
  candidates stand.

The BCM sheet still wins when it's ahead or final (profiles with client, offers, staffed, cancelled,
incorrect). A sheet still saying Coverage required doesn't undo newer panel progress (`sheet_yields`).
Every change is a stage event, so interview progress also counts as progress for aging.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.config import get_settings
from app.core.enums import (
    APP_PROGRESS,
    FINISHED,
    PROGRESS_ORDER,
    DemandStatus,
    InterviewOutcome,
    InterviewStatus,
    StageOrigin,
)
from app.core.security import Actor
from app.models import Account, Candidate, Demand, Interview, OfferApproval, StageEvent, User
from app.services import margin_service, notify_service
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
        if c.client_outcome == InterviewOutcome.REJECT.value:
            continue  # the client said no
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
        final = not demand.client_interview_required or any(
            s.client_outcome == InterviewOutcome.SELECT.value for s, _ in selected
        )
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


# --- Client interview result -----------------------------------------------------------------------

CLIENT_STAGES = (DemandStatus.PANEL_SELECTED, DemandStatus.PROFILES_WITH_CLIENT)


class ClientDecisionError(ValueError):
    pass


def awaiting_client(db: Session, demand: Demand) -> list[Candidate]:
    """Candidates whose client interview result is still to be recorded: selected by the panel (or put
    to the client by staffing, per the BCM sheet), no client result and no offer approval yet."""
    cur = demand.status_enum
    if cur not in CLIENT_STAGES:
        return []
    have = set(db.scalars(select(OfferApproval.candidate_id).where(OfferApproval.demand_id == demand.id)))
    out = []
    for c in db.scalars(select(Candidate).where(Candidate.demand_id == demand.id).order_by(Candidate.name)):
        if c.client_outcome is not None or c.id in have:
            continue
        ivs = list(db.scalars(select(Interview).where(Interview.candidate_id == c.id)))
        done = [iv for iv in ivs if iv.status == InterviewStatus.COMPLETED.value]
        last = max(done, key=lambda iv: (iv.round_no, iv.submitted_at)) if done else None
        if last is not None and last.outcome == InterviewOutcome.REJECT.value:
            continue
        panel_selected = last is not None and last.outcome == InterviewOutcome.SELECT.value
        if panel_selected or (cur is DemandStatus.PROFILES_WITH_CLIENT and last is None):
            out.append(c)
    return out


def can_record_client(actor: Actor, demand: Demand) -> bool:
    return actor.id == demand.owner_id and demand.status_enum not in FINISHED


def start_client_interview(db: Session, actor: Actor, demand: Demand) -> None:
    """The demand owner records that the client has started interviewing the panel's selection."""
    if not can_record_client(actor, demand):
        raise ClientDecisionError("Only the demand's owner records the client interview.")
    waiting = awaiting_client(db, demand)
    if demand.status_enum is not DemandStatus.PANEL_SELECTED or not waiting:
        raise ClientDecisionError("The client interview starts once the panel has selected a candidate.")
    record_stage(db, demand, DemandStatus.PROFILES_WITH_CLIENT, actor.id, StageOrigin.APP)
    for c in waiting:
        c.current_stage = DemandStatus.PROFILES_WITH_CLIENT.label
    ref = f"{demand.gtd_req_id} | {demand.app_ref}" if demand.gtd_req_id else demand.app_ref
    names = ", ".join(c.name for c in waiting)
    notify_service.safely(
        mail.send,
        mail.Mail(
            to=margin_service._team_admins(db, demand.account_id),
            cc=[actor.email],
            subject=f"[{ref}] Client interview started",
            text=(
                f"{actor.name} recorded that the client interview has started on {demand.app_ref} "
                f"({demand.name}) for {names}.\n\n"
                f"{get_settings().app_base_url}/demands/{demand.app_ref}"
            ),
        ),
    )
    db.commit()


def record_client_decision(
    db: Session, actor: Actor, demand: Demand, candidate_id: int, outcome: str, channel: str, note: str
) -> Candidate:
    """The demand owner records what the client decided after its interview."""
    if not can_record_client(actor, demand):
        raise ClientDecisionError("Only the demand's owner records the client's decision.")
    cand = next((c for c in awaiting_client(db, demand) if c.id == candidate_id), None)
    if cand is None:
        raise ClientDecisionError("Pick a candidate who is waiting for the client's decision.")
    if outcome not in (InterviewOutcome.SELECT.value, InterviewOutcome.REJECT.value):
        raise ClientDecisionError("Say whether the client selected the candidate or not.")
    account = db.get_one(Account, demand.account_id)
    selected = outcome == InterviewOutcome.SELECT.value
    known = margin_service.known_channel(account, channel) or cand.channel
    if not selected and not note.strip():
        raise ClientDecisionError("Add a short note on why the client did not select the candidate.")

    cand.client_outcome = outcome
    cand.client_decided_at = datetime.now(UTC)
    cand.client_decided_by = actor.id
    cand.client_note = note.strip() or None
    if selected:
        cand.channel = known
        record_stage(db, demand, DemandStatus.OFFER_IN_PROCESS, actor.id, StageOrigin.APP)
        margin_service.ensure_offer(db, account, demand, cand.name, known)  # mails whoever decides it
        outcome_text = "selected by the client · offer approval raised"
    else:
        cand.current_stage = "Not selected by the client"
        db.flush()
        if not awaiting_client(db, demand):  # nobody else is with the client: back to where the rest stand
            ev = evidence(db, demand)
            back = ev.stage if ev is not None else DemandStatus.COVERAGE_REQUIRED
            record_stage(db, demand, back, actor.id, StageOrigin.APP)
        outcome_text = "not selected by the client"
    ref = f"{demand.gtd_req_id} | {demand.app_ref}" if demand.gtd_req_id else demand.app_ref
    notify_service.safely(
        mail.send,
        mail.Mail(
            to=margin_service._team_admins(db, account.id),
            cc=[actor.email],
            subject=f"[{ref}] Client interview: {cand.name} {'selected' if selected else 'not selected'}",
            text=(
                f"{actor.name} recorded the client's decision on {demand.app_ref} ({demand.name}): "
                f"{cand.name} was {outcome_text}.\n"
                + (f"Note: {cand.client_note}\n" if cand.client_note else "")
                + f"The demand is now {demand.status_enum.full}.\n\n"
                f"{get_settings().app_base_url}/demands/{demand.app_ref}"
            ),
        ),
    )
    db.commit()
    return cand


def _client_said_no(db: Session, demand: Demand) -> bool:
    """The owner recorded a client rejection and nobody else is waiting on the client."""
    rejected = db.scalar(
        select(Candidate.id).where(
            Candidate.demand_id == demand.id, Candidate.client_outcome == InterviewOutcome.REJECT.value
        )
    )
    return rejected is not None


def sheet_yields(db: Session, demand: Demand, sheet_stage: DemandStatus | None) -> bool:
    """True when the BCM sheet's stage is behind progress the app already recorded: panel progress,
    or an offer the owner marked as accepted. A sheet stage further on always wins."""
    cur = demand.status_enum
    if sheet_stage not in PROGRESS_ORDER or cur not in PROGRESS_ORDER:
        return False
    if (
        sheet_stage is DemandStatus.PROFILES_WITH_CLIENT
        and _rank(cur) < _rank(sheet_stage)
        and _client_said_no(db, demand)
    ):
        return True  # the sheet still says "with the client" after the owner recorded the client's no
    if _rank(sheet_stage) >= _rank(cur):
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
