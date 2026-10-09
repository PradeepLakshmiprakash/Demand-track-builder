"""What proactive, non-billable positions cost the account.

A Proactive position (shadow, bench, NGT) is non-billable: the client isn't paying for it. From its
start date it costs the account every working day, whether or not a candidate has joined, until the
demand owner marks it billable (the client has started billing). That cost is its own figure; it is
never part of revenue lost.

    cost so far = cost rate × billable hours a day × working days from the start date

The cost rate is the rate card's cost per hour for the position's practice and grade (an offer on the
position carries the same figure, as priced on its date).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core import mail
from app.core.config import get_settings
from app.core.enums import Decision, DemandStatus, Role
from app.core.security import Actor
from app.models import Account, Candidate, Demand, OfferApproval, User, member_of
from app.services import notify_service, rate_card_service
from app.services.demand_service import account_today
from app.services.loss_service import working_days

MONTH_DAYS = 21  # working days in a month, for "cost per month from here"
NOT_COSTED = (DemandStatus.DRAFT, DemandStatus.CANCELLED, DemandStatus.CLOSED)


class CostingError(ValueError):
    pass


@dataclass
class Cost:
    demand: Demand
    start: date
    until: date | None  # billable from this day: costing stopped
    working_days: int
    rate: Decimal | None  # per hour; None when neither an offer nor the rate card gives one
    source: str | None  # "offer" or "rate card"
    so_far: Decimal | None
    monthly: Decimal | None  # only while it is still costing
    resource: str | None  # the candidate on the position, once there is an offer

    @property
    def active(self) -> bool:
        return self.until is None


def _rate_card_rate(db: Session, d: Demand, on: date) -> Decimal | None:
    if not d.grade:
        return None
    rate = rate_card_service.lookup(
        db, d.account_id, grade=d.grade, practice=d.practice, on=on, region=d.region
    )
    return Decimal(rate.cost_rate) if rate is not None else None


def costs(db: Session, account_id: int, today: date | None = None) -> dict[int, Cost]:
    """Cost per proactive, non-billable demand whose start date has passed, keyed by demand id."""
    today = today or account_today(db, account_id)
    account = db.get_one(Account, account_id)
    hours = Decimal(str(account.settings.billable_hours_per_day))
    demands = [
        d
        for d in db.scalars(
            select(Demand)
            .where(
                Demand.account_id == account_id,
                Demand.position_type == "Non-billable",
                Demand.status.notin_([s.value for s in NOT_COSTED]),
                Demand.start_date.is_not(None),
            )
            .options(selectinload(Demand.business_unit), selectinload(Demand.owner))
        )
        if d.is_proactive_nb
    ]
    offers: dict[int, OfferApproval] = {}
    if demands:
        for o in db.scalars(
            select(OfferApproval)
            .where(OfferApproval.demand_id.in_([d.id for d in demands]), OfferApproval.cost_rate.is_not(None))
            .order_by(OfferApproval.id)
        ):
            cur = offers.get(o.demand_id)
            if cur is None or cur.decision != Decision.APPROVED.value:  # an approved offer wins
                offers[o.demand_id] = o
    cand_ids = [o.candidate_id for o in offers.values()]
    names = (
        {c.id: c.name for c in db.scalars(select(Candidate).where(Candidate.id.in_(cand_ids)))}
        if cand_ids
        else {}
    )
    out: dict[int, Cost] = {}
    for d in demands:
        start = d.start_date
        assert start is not None
        end = min(d.billable_from, today) if d.billable_from else today
        if end <= start:
            continue
        offer = offers.get(d.id)
        rate = offer.cost_rate if offer else _rate_card_rate(db, d, today)
        wd = working_days(start, end)
        daily = rate * hours if rate is not None else None
        out[d.id] = Cost(
            demand=d,
            start=start,
            until=d.billable_from,
            working_days=wd,
            rate=rate,
            source=("offer" if offer else "rate card") if rate is not None else None,
            so_far=daily * wd if daily is not None else None,
            monthly=daily * MONTH_DAYS if daily is not None and d.billable_from is None else None,
            resource=names.get(offer.candidate_id) if offer else None,
        )
    return out


def can_mark_billable(actor: Actor, demand: Demand) -> bool:
    """The demand's owner, on a proactive non-billable position that is live."""
    return actor.id == demand.owner_id and demand.is_proactive_nb and demand.status_enum not in NOT_COSTED


def mark_billable(db: Session, actor: Actor, demand: Demand, when: date | None) -> None:
    """The client has started billing: costing stops from `when`. `None` undoes it."""
    if not can_mark_billable(actor, demand):
        raise CostingError("Only the demand's owner marks a proactive, non-billable position as billable.")
    today = account_today(db, demand.account_id)
    if when is not None:
        if demand.start_date is None or when < demand.start_date:
            raise CostingError("Billing can't start before the position's start date.")
        if when > today:
            raise CostingError(
                "Mark it billable on or after the day the client's billing starts, not before."
            )
    demand.billable_from = when
    demand.billable_marked_by = actor.id if when else None
    demand.billable_marked_at = datetime.now(UTC) if when else None
    db.flush()
    ref = f"{demand.gtd_req_id} | {demand.app_ref}" if demand.gtd_req_id else demand.app_ref
    admins = [u.email for u in db.scalars(select(User).where(member_of(demand.account_id, Role.ADMIN)))]
    what = (
        f"is billable from {when:%d %b %Y}: costing stops"
        if when
        else "is non-billable again: costing resumes"
    )
    notify_service.safely(
        mail.send,
        mail.Mail(
            to=admins,
            cc=[actor.email],
            subject=f"[{ref}] {'Now billable' if when else 'Back to non-billable'}",
            text=(
                f"{actor.name} recorded that {demand.app_ref} ({demand.name}) {what}.\n\n"
                f"{get_settings().app_base_url}/demands/{demand.app_ref}"
            ),
        ),
    )
    db.commit()
