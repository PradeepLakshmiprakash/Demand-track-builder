"""Offer margin approvals (flow-artifact §8).

When a demand reaches Offer in process, its candidate's offer is priced:
    margin = (client bill rate − vendor cost rate) ÷ client bill rate
with the cost from the rate card in force on the offer date. At or above the account's cut-off (30% for
Discover) the admin demand owner approves or declines; below it, leadership decides at their
discretion (confirmed 24 Sep). Every decision records approver, margin and time.

An offer whose bill rate, supply channel or rate card entry is missing waits unpriced, with the reason
shown, and is re-priced whenever the approvals screen loads or the rate card changes.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.account_config import AccountConfig
from app.core.enums import ApprovalRoute, Decision, DemandStatus, Role
from app.core.security import Actor
from app.models import Account, Candidate, Demand, OfferApproval
from app.services import rate_card_service

OFFER_STAGES = (DemandStatus.OFFER_IN_PROCESS, DemandStatus.OFFER_IN_MARKET)
DECIDERS = {ApprovalRoute.ADMIN.value: (Role.ADMIN,), ApprovalRoute.LEADERSHIP.value: (Role.LEADERSHIP,)}


class ApprovalError(ValueError):
    pass


def channel_for_source(cfg: AccountConfig, source: str | None) -> str | None:
    """Map the DP sheet's Source cell (e.g. 'VMS', 'Sogeti') to a supply channel key."""
    s = (source or "").strip().casefold()
    if not s:
        return None
    for c in cfg.supply_channels:
        names = [c.key.casefold(), c.label.casefold(), c.sheet_marker.casefold()]
        if any(s == n or s in n.split() or n in s for n in names if n):
            return c.key
    return None


def _local_date(account: Account, when: datetime) -> date:
    return when.astimezone(ZoneInfo(account.settings.timezone)).date()


def price(db: Session, account: Account, approval: OfferApproval, demand: Demand) -> None:
    """Fill bill, cost, margin and route, or leave it unpriced with the reason."""
    on = approval.priced_on or _local_date(account, approval.created_at or datetime.now(UTC))
    approval.priced_on = on
    approval.bill_rate = demand.client_rate
    approval.cost_rate = approval.margin_pct = approval.route = None
    cfg = account.settings
    channel_label = next(
        (c.label for c in cfg.supply_channels if c.key == approval.channel), approval.channel
    )
    if not demand.client_rate:
        approval.blocked_reason = "The demand has no client bill rate. Add it on the demand."
        return
    if not approval.channel:
        approval.blocked_reason = "The supply channel isn't known. Set it on this offer."
        return
    rate = rate_card_service.lookup(
        db, account.id, grade=demand.grade or "", practice=demand.practice, region=demand.region or "",
        channel=approval.channel, on=on,
    )  # fmt: skip
    if rate is None:
        approval.blocked_reason = (
            f"No rate card entry for {demand.grade} · {demand.practice} · {demand.region} · {channel_label} "
            f"on {on:%d %b %Y}."
        )
        return
    bill = Decimal(demand.client_rate)
    margin = ((bill - rate.cost_rate) / bill * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    approval.cost_rate, approval.margin_pct = rate.cost_rate, margin
    approval.route = (
        ApprovalRoute.ADMIN if margin >= account.margin_threshold else ApprovalRoute.LEADERSHIP
    ).value
    approval.blocked_reason = None


def ensure_offer(
    db: Session, account: Account, demand: Demand, candidate_name: str, channel: str | None
) -> OfferApproval | None:
    """Create the candidate (if new) and a pending approval for their offer, once per candidate."""
    name = candidate_name.strip().splitlines()[0].strip()[:160] if candidate_name.strip() else ""
    if not name:
        return None
    cand = next(
        (c for c in db.scalars(select(Candidate).where(Candidate.demand_id == demand.id))
         if c.name.casefold() == name.casefold()),
        None,
    )  # fmt: skip
    if cand is None:
        cand = Candidate(
            demand_id=demand.id, name=name, channel=channel, current_stage=DemandStatus.OFFER_IN_PROCESS
        )
        db.add(cand)
        db.flush()
    already = db.scalar(select(OfferApproval.id).where(OfferApproval.candidate_id == cand.id))
    if already is not None:
        return None
    approval = OfferApproval(demand_id=demand.id, candidate_id=cand.id, channel=channel or cand.channel,
                             created_at=datetime.now(UTC))  # fmt: skip
    db.add(approval)
    price(db, account, approval, demand)
    db.flush()
    return approval


def request(db: Session, actor: Actor, demand_id: int, candidate_name: str, channel: str) -> OfferApproval:
    """The admin raises an approval by hand (e.g. the DP sheet row had no candidate name)."""
    demand = db.get(Demand, demand_id)
    if demand is None or demand.account_id != actor.account_id:
        raise ApprovalError("Demand not found.")
    if demand.status_enum not in OFFER_STAGES:
        raise ApprovalError(f"{demand.app_ref} isn't at the offer stage.")
    account = db.get_one(Account, actor.account_id)
    if channel not in {c.key for c in account.settings.supply_channels}:
        raise ApprovalError("Choose the supply channel.")
    approval = ensure_offer(db, account, demand, candidate_name, channel)
    if approval is None:
        raise ApprovalError("Enter the candidate's name (an approval for that candidate already exists?).")
    db.commit()
    return approval


def set_channel(db: Session, actor: Actor, approval_id: int, channel: str) -> OfferApproval:
    approval, demand, account = _pending(db, actor, approval_id)
    if channel not in {c.key for c in account.settings.supply_channels}:
        raise ApprovalError("Choose the supply channel.")
    approval.channel = channel
    price(db, account, approval, demand)
    db.commit()
    return approval


def reprice_pending(db: Session, account_id: int) -> int:
    account = db.get_one(Account, account_id)
    n = 0
    for approval in db.scalars(
        select(OfferApproval)
        .join(Demand)
        .where(Demand.account_id == account_id, OfferApproval.decision.is_(None))
    ):
        before = (approval.margin_pct, approval.route, approval.blocked_reason)
        price(db, account, approval, db.get_one(Demand, approval.demand_id))
        n += before != (approval.margin_pct, approval.route, approval.blocked_reason)
    db.commit()
    return n


def _pending(db: Session, actor: Actor, approval_id: int) -> tuple[OfferApproval, Demand, Account]:
    approval = db.get(OfferApproval, approval_id)
    demand = db.get(Demand, approval.demand_id) if approval else None
    if approval is None or demand is None or demand.account_id != actor.account_id:
        raise ApprovalError("Offer approval not found.")
    if approval.decision is not None:
        raise ApprovalError("This offer has already been decided.")
    return approval, demand, db.get_one(Account, actor.account_id)


def can_decide(actor: Actor, approval: OfferApproval) -> bool:
    return approval.decision is None and approval.route is not None and actor.role in DECIDERS[approval.route]


def decide(db: Session, actor: Actor, approval_id: int, decision: str, comment: str | None) -> OfferApproval:
    approval, demand, account = _pending(db, actor, approval_id)
    price(db, account, approval, demand)  # decide on current numbers
    if approval.route is None:
        raise ApprovalError(f"Can't decide yet: {approval.blocked_reason}")
    if not can_decide(actor, approval):
        who = "the admin demand owner" if approval.route == ApprovalRoute.ADMIN.value else "leadership"
        cut = f"{account.margin_threshold:g}%"
        raise ApprovalError(f"A {approval.margin_pct}% margin offer is decided by {who} (cut-off {cut}).")
    try:
        d = Decision(decision)
    except ValueError as e:
        raise ApprovalError("Approve or decline.") from e
    comment = (comment or "").strip() or None
    below = approval.route == ApprovalRoute.LEADERSHIP.value
    if not comment and (d is Decision.DECLINED or below):
        raise ApprovalError(
            "Add a comment: why it's declined."
            if d is Decision.DECLINED
            else "Add a comment: why the exception."
        )
    approval.decision, approval.comment = d.value, comment
    approval.approver_id, approval.decided_at = actor.id, datetime.now(UTC)
    db.commit()
    return approval


@dataclass
class Board:
    mine: list[tuple[OfferApproval, Demand, Candidate]]  # waiting for this actor
    others: list[tuple[OfferApproval, Demand, Candidate]]  # waiting for the other route
    blocked: list[tuple[OfferApproval, Demand, Candidate]]
    decided: list[tuple[OfferApproval, Demand, Candidate]]
    without_request: list[Demand]  # at the offer stage with no approval yet


def board(db: Session, actor: Actor) -> Board:
    reprice_pending(db, actor.account_id)
    rows = db.execute(
        select(OfferApproval, Demand, Candidate)
        .join(Demand, Demand.id == OfferApproval.demand_id)
        .join(Candidate, Candidate.id == OfferApproval.candidate_id)
        .where(Demand.account_id == actor.account_id)
        .order_by(OfferApproval.created_at.desc())
    ).all()
    b = Board([], [], [], [], [])
    for a, d, c in rows:
        item = (a, d, c)
        if a.decision is not None:
            b.decided.append(item)
        elif a.route is None:
            b.blocked.append(item)
        elif can_decide(actor, a):
            b.mine.append(item)
        else:
            b.others.append(item)
    has = {d.id for _, d, _ in rows}
    b.without_request = [
        d
        for d in db.scalars(
            select(Demand)
            .where(
                Demand.account_id == actor.account_id, Demand.status == DemandStatus.OFFER_IN_PROCESS.value
            )
            .order_by(Demand.app_ref)
        )
        if d.id not in has
    ]
    return b
