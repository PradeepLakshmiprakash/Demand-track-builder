"""Vendor rate card (flow-artifact §8.1): cost per hour by grade, practice, region and supply channel.

Rates are dated and never edited in place, so an old offer's margin can always be recomputed with the
rate that applied then. Adding a rate for a key that already has one open-ended row closes that row the
day before the new one starts. A blank practice means "any practice"; a practice-specific rate wins.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.models import Account, RateCard


class RateCardError(ValueError):
    pass


@dataclass(frozen=True)
class RateKey:
    grade: str
    practice: str | None
    region: str
    channel: str


def lookup(
    db: Session, account_id: int, *, grade: str, practice: str | None, region: str, channel: str, on: date
) -> RateCard | None:
    """The rate in force on `on`: practice-specific first, then the any-practice row."""
    rows = db.scalars(
        select(RateCard).where(
            RateCard.account_id == account_id,
            RateCard.grade == grade,
            RateCard.region == region,
            RateCard.channel == channel,
            or_(RateCard.practice == practice, RateCard.practice.is_(None)),
            RateCard.effective_from <= on,
            or_(RateCard.effective_to.is_(None), RateCard.effective_to >= on),
        )
    ).all()
    specific = [r for r in rows if r.practice is not None]
    candidates = specific or list(rows)
    return candidates[0] if candidates else None


def _same_key(key: RateKey) -> list[ColumnElement[bool]]:
    return [
        RateCard.grade == key.grade,
        RateCard.region == key.region,
        RateCard.channel == key.channel,
        RateCard.practice.is_(None) if key.practice is None else RateCard.practice == key.practice,
    ]


def add_rate(
    db: Session,
    account_id: int,
    actor_id: int,
    *,
    grade: str,
    practice: str | None,
    region: str,
    channel: str,
    cost_rate: str,
    effective_from: date,
    effective_to: date | None = None,
) -> RateCard:
    cfg = db.get_one(Account, account_id).settings
    practice = practice or None
    if grade not in cfg.grades:
        raise RateCardError(f"Grade '{grade}' isn't in the account's list.")
    if practice is not None and practice not in cfg.practices:
        raise RateCardError(f"Practice '{practice}' isn't in the account's list.")
    if region not in cfg.regions:
        raise RateCardError(f"Region '{region}' isn't in the account's list.")
    if channel not in {c.key for c in cfg.supply_channels}:
        raise RateCardError("Choose a supply channel.")
    try:
        cost = Decimal(cost_rate).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as e:
        raise RateCardError("Cost rate must be a number.") from e
    if cost < 0 or cost > 10000:
        raise RateCardError("Cost rate must be between 0 and 10,000 per hour.")
    if effective_to is not None and effective_to < effective_from:
        raise RateCardError("The end date is before the start date.")

    key = RateKey(grade, practice, region, channel)
    existing = list(db.scalars(select(RateCard).where(RateCard.account_id == account_id, *_same_key(key))))
    for r in existing:
        starts_before = r.effective_from < effective_from
        open_ended = r.effective_to is None
        if open_ended and starts_before and effective_to is None:
            r.effective_to = effective_from - timedelta(days=1)  # the new rate takes over
            continue
        new_end = effective_to or date.max
        old_end = r.effective_to or date.max
        if r.effective_from <= new_end and effective_from <= old_end:
            raise RateCardError(
                f"Overlaps the {r.cost_rate} rate from {r.effective_from:%d %b %Y}"
                + (f" to {r.effective_to:%d %b %Y}" if r.effective_to else "")
                + ". Rates are dated history: add a new rate from a later date instead."
            )
    row = RateCard(
        account_id=account_id,
        grade=grade,
        practice=practice,
        region=region,
        channel=channel,
        cost_rate=cost,
        effective_from=effective_from,
        effective_to=effective_to,
        created_by=actor_id,
    )
    db.add(row)
    db.commit()
    return row


def end_rate(db: Session, account_id: int, rate_id: int, last_day: date) -> RateCard:
    row = db.get(RateCard, rate_id)
    if row is None or row.account_id != account_id:
        raise RateCardError("Rate not found.")
    if last_day < row.effective_from:
        raise RateCardError("A rate can't end before it starts.")
    if row.effective_to is not None and row.effective_to <= last_day:
        raise RateCardError("That rate already ends by then.")
    row.effective_to = last_day
    db.commit()
    return row


def listing(db: Session, account_id: int, *, on: date, include_history: bool = False) -> list[RateCard]:
    stmt = select(RateCard).where(RateCard.account_id == account_id)
    if not include_history:
        stmt = stmt.where(or_(RateCard.effective_to.is_(None), RateCard.effective_to >= on))
    return list(
        db.scalars(
            stmt.order_by(
                RateCard.channel, RateCard.region, RateCard.grade, RateCard.practice, RateCard.effective_from
            )
        )
    )
