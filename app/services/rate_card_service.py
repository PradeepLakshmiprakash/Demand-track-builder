"""Rate card: what a resource costs per hour, by practice and grade.

The same concept as Acquisition Central's cost card: the client's rate belongs to each position, the
cost comes from this card for the position's practice and grade, and one margin threshold decides who
approves an offer. There is one card for the account: supply channel and region don't change the cost.

Rates are dated and never edited in place, so an old offer's margin can always be recomputed with the
rate that applied then. Adding a rate for a practice and grade that already has an open-ended one closes
that one the day before the new one starts.
"""

import csv
import io
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import openpyxl
from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.models import Account, RateCard


class RateCardError(ValueError):
    pass


@dataclass(frozen=True)
class RateKey:
    grade: str
    practice: str | None


def _in_force(on: date) -> list[ColumnElement[bool]]:
    return [RateCard.effective_from <= on, or_(RateCard.effective_to.is_(None), RateCard.effective_to >= on)]


def lookup(db: Session, account_id: int, *, grade: str, practice: str | None, on: date) -> RateCard | None:
    """The cost in force on `on` for this practice and grade. A rate entered for "any practice" (kept
    from older cards) applies where the practice has none of its own."""
    rows = db.scalars(
        select(RateCard).where(
            RateCard.account_id == account_id,
            RateCard.grade == grade,
            or_(RateCard.practice == practice, RateCard.practice.is_(None)),
            *_in_force(on),
        )
    ).all()
    specific = [r for r in rows if r.practice is not None]
    candidates = specific or list(rows)
    return candidates[0] if candidates else None


def grid(db: Session, account_id: int, on: date) -> dict[tuple[str, str | None], RateCard]:
    """Every rate in force on `on`, by (grade, practice); practice None is the "any practice" rate."""
    rows = db.scalars(select(RateCard).where(RateCard.account_id == account_id, *_in_force(on)))
    return {(r.grade, r.practice): r for r in rows}


def _same_key(key: RateKey) -> list[ColumnElement[bool]]:
    return [
        RateCard.grade == key.grade,
        RateCard.practice.is_(None) if key.practice is None else RateCard.practice == key.practice,
    ]


def add_rate(
    db: Session,
    account_id: int,
    actor_id: int,
    *,
    grade: str,
    practice: str | None,
    cost_rate: str,
    effective_from: date,
    effective_to: date | None = None,
    commit: bool = True,
) -> RateCard:
    cfg = db.get_one(Account, account_id).settings
    practice = practice or None
    if grade not in cfg.grades:
        raise RateCardError(f"Grade '{grade}' isn't in the account's list.")
    if practice is None:
        raise RateCardError("Choose the practice: the cost is set by practice and grade.")
    if practice not in cfg.practices:
        raise RateCardError(f"Practice '{practice}' isn't in the account's list.")
    try:
        cost = Decimal(cost_rate).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError) as e:
        raise RateCardError("Cost per hour must be a number.") from e
    if cost < 0 or cost > 10000:
        raise RateCardError("Cost per hour must be between 0 and 10,000.")
    if effective_to is not None and effective_to < effective_from:
        raise RateCardError("The end date is before the start date.")

    key = RateKey(grade, practice)
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
        cost_rate=cost,
        effective_from=effective_from,
        effective_to=effective_to,
        created_by=actor_id,
    )
    db.add(row)
    db.flush()  # later rows in the same upload must see this one
    if commit:
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
    return list(db.scalars(stmt.order_by(RateCard.practice, RateCard.grade, RateCard.effective_from)))


# --- The calculator: what a client rate affords ------------------------------------------------------


@dataclass
class Offering:
    practice: str
    grade: str
    cost: Decimal
    margin_pct: Decimal
    fits: bool  # clears the margin being held


def margin_pct(bill: Decimal, cost: Decimal) -> Decimal:
    """margin % = (client rate − cost) ÷ client rate"""
    return ((bill - cost) / bill * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def ceiling(bill: Decimal, hold: Decimal) -> Decimal:
    """The highest cost per hour that still keeps `hold` per cent on this client rate."""
    return (bill * (1 - hold / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def offerings(db: Session, account_id: int, bill: Decimal, hold: Decimal, on: date) -> list[Offering]:
    """Every practice and grade on the card, priced against this client rate."""
    cfg = db.get_one(Account, account_id).settings
    card = grid(db, account_id, on)
    out = []
    for practice in cfg.practices:
        for grade in cfg.grades:
            rate = card.get((grade, practice)) or card.get((grade, None))
            if rate is None:
                continue
            m = margin_pct(bill, Decimal(rate.cost_rate))
            out.append(Offering(practice, grade, Decimal(rate.cost_rate), m, m >= hold))
    return out


# --- Team and pod contribution margin ------------------------------------------------------------------

MONTH_DAYS = 21  # working days in a month, for monthly figures


@dataclass
class Member:
    """One line of a team or a pod: so many people of a grade in a practice."""

    practice: str
    grade: str
    count: int
    rate: Decimal | None = None  # team: what the client pays per hour for each of them
    allocation: Decimal = Decimal(100)  # pod: the share of their time on the pod, per cent
    cost: Decimal | None = None  # per hour each, from the rate card; None when the card has none

    @property
    def fte(self) -> Decimal:
        return self.count * self.allocation / 100

    @property
    def revenue(self) -> Decimal | None:
        return self.rate * self.count if self.rate is not None else None

    @property
    def total_cost(self) -> Decimal | None:
        return self.cost * self.fte if self.cost is not None else None

    @property
    def margin_pct(self) -> Decimal | None:
        if self.rate is None or self.cost is None or not self.rate:
            return None
        return margin_pct(self.rate, self.cost)

    @property
    def priced(self) -> bool:
        return self.cost is not None


@dataclass
class Contribution:
    """A team's or a pod's figures, per hour unless said otherwise."""

    members: list[Member]
    revenue: Decimal
    cost: Decimal
    hours_per_month: Decimal
    threshold: Decimal

    @property
    def margin(self) -> Decimal:
        return self.revenue - self.cost

    @property
    def margin_pct(self) -> Decimal | None:
        return margin_pct(self.revenue, self.cost) if self.revenue > 0 else None

    @property
    def fits(self) -> bool:
        return self.margin_pct is not None and self.margin_pct >= self.threshold

    @property
    def headcount(self) -> Decimal:
        return sum((m.fte for m in self.members), Decimal(0))

    @property
    def unpriced(self) -> list[Member]:
        return [m for m in self.members if not m.priced]

    def monthly(self, per_hour: Decimal) -> Decimal:
        return (per_hour * self.hours_per_month).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    @property
    def target_revenue(self) -> Decimal | None:
        """The revenue per hour at which this cost leaves exactly the account's margin."""
        if self.threshold >= 100:
            return None
        return (self.cost / (1 - self.threshold / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cost_members(db: Session, account_id: int, members: list[Member], on: date) -> list[Member]:
    """Fill each line's cost per hour from the rate card."""
    card = grid(db, account_id, on)
    for m in members:
        rate = card.get((m.grade, m.practice)) or card.get((m.grade, None))
        m.cost = Decimal(rate.cost_rate) if rate is not None else None
    return members


def team_contribution(db: Session, account: Account, members: list[Member], on: date) -> Contribution:
    """Team contribution margin: each role has its own client rate; the team's margin is everything
    billed less everything it costs, over everything billed. Lines without a cost or a rate are left out
    of the totals (and listed, so the gap is visible)."""
    cost_members(db, account.id, members, on)
    counted = [m for m in members if m.cost is not None and m.rate is not None]
    hours = Decimal(str(account.settings.billable_hours_per_day)) * MONTH_DAYS
    return Contribution(
        members,
        revenue=sum((m.rate * m.count for m in counted if m.rate is not None), Decimal(0)),
        cost=sum((m.cost * m.count for m in counted if m.cost is not None), Decimal(0)),
        hours_per_month=hours,
        threshold=Decimal(account.margin_threshold),
    )


def pod_contribution(
    db: Session, account: Account, members: list[Member], price_per_month: Decimal, on: date
) -> Contribution:
    """Pod contribution margin: the client pays one price a month for the whole pod; people can be on
    it part-time. The pod's cost is each member's cost for their share of time; its margin is the price
    less that cost, over the price."""
    cost_members(db, account.id, members, on)
    hours = Decimal(str(account.settings.billable_hours_per_day)) * MONTH_DAYS
    cost = sum((m.cost * m.fte for m in members if m.cost is not None), Decimal(0))
    return Contribution(
        members,
        revenue=price_per_month / hours if hours else Decimal(0),
        cost=cost,
        hours_per_month=hours,
        threshold=Decimal(account.margin_threshold),
    )


@dataclass
class Suggestion:
    offering: Offering
    why: list[str]  # "Closest to the 30% margin", "Highest margin"…
    where: str  # "your choice", "same practice", or the tech stack a related practice shares
    chosen: bool = False


def _closest(rows: list[Offering]) -> Offering | None:
    """The one that clears the margin by the least; if none clears it, the one that comes nearest."""
    fits = [r for r in rows if r.fits]
    if fits:
        return min(fits, key=lambda r: r.margin_pct)
    return max(rows, key=lambda r: r.margin_pct) if rows else None


def suggestions(
    rows: list[Offering],
    practice: str,
    grade: str,
    threshold: Decimal,
    shared: Callable[[str, str], list[str]],
) -> list[Suggestion]:
    """A short list beside the grade asked about: in the same practice, the grade closest to the margin
    and the one with the highest margin; and the same two among the practices that take the same
    tech stack, at the same grade. `shared(a, b)` gives the tech stacks two practices have in common (the
    account's own mapping: AccountConfig.shared_stacks). Never more than five rows."""
    out: list[Suggestion] = []

    def add(o: Offering | None, why: str, where: str, chosen: bool = False) -> None:
        if o is None:
            return
        for s in out:
            if s.offering is o:
                if why and why not in s.why:
                    s.why.append(why)
                return
        out.append(Suggestion(o, [why] if why else [], where, chosen))

    near = f"Closest to the {float(threshold):g}% margin"
    mine = [r for r in rows if r.practice == practice]
    add(next((r for r in mine if r.grade == grade), None), "", "Your choice", True)
    add(_closest(mine), near, "Same practice")
    add(max(mine, key=lambda r: r.margin_pct) if mine else None, "Highest margin", "Same practice")
    related = [
        r for r in rows if r.grade == grade and r.practice != practice and shared(practice, r.practice)
    ]
    for o, why in (
        (_closest(related), near),
        (max(related, key=lambda r: r.margin_pct, default=None), "Highest margin"),
    ):
        if o is not None:
            add(o, why, "Also takes " + ", ".join(shared(practice, o.practice)))
    return out


# --- Bulk upload -----------------------------------------------------------------------------------

UPLOAD_COLUMNS = ["Practice", "Grade", "Cost per hour", "From", "Until"]


def _cell_date(v: object) -> date | None:
    if v is None or str(v).strip() == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise RateCardError(f"'{s}' isn't a date")


def _read_rows(filename: str, data: bytes) -> list[dict[str, object]]:
    name = filename.lower()
    if name.endswith(".csv"):
        return [dict(r) for r in csv.DictReader(io.StringIO(data.decode("utf-8-sig")))]
    if name.endswith(".xlsx"):
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as e:  # openpyxl raises several types for bad input
            raise RateCardError("That isn't an Excel .xlsx file the app can read.") from e
        rows = list(wb.worksheets[0].iter_rows(values_only=True))
        wb.close()
        if not rows:
            return []
        head = [str(h or "").strip() for h in rows[0]]
        return [dict(zip(head, r, strict=False)) for r in rows[1:] if any(c not in (None, "") for c in r)]
    raise RateCardError("Upload a .csv or .xlsx file.")


def import_rates(db: Session, account_id: int, actor_id: int, filename: str, data: bytes) -> int:
    """Add many rates at once, all or nothing. Same rules as adding one: dated, no overlaps."""
    rows = _read_rows(filename, data)
    if not rows:
        raise RateCardError("The file has no rates.")
    columns = {k.strip().casefold(): k for k in rows[0]}
    missing = [c for c in UPLOAD_COLUMNS if c != "Until" and c.casefold() not in columns]
    if missing:
        raise RateCardError(f"Missing columns: {', '.join(missing)}. Expected: {', '.join(UPLOAD_COLUMNS)}.")
    cfg = db.get_one(Account, account_id).settings
    practice_of = {p.casefold(): p for p in cfg.practices}
    errors: list[str] = []
    for n, row in enumerate(rows, start=2):  # row 1 is the header

        def col(name: str, row: dict[str, object] = row) -> str:
            key = columns.get(name.casefold())
            value = row.get(key) if key else None
            return "" if value is None else str(value).strip()

        try:
            start = _cell_date(row.get(columns["from"]))
            if start is None:
                raise RateCardError("no From date")
            practice = col("Practice")
            add_rate(
                db,
                account_id,
                actor_id,
                grade=col("Grade").upper(),
                practice=practice_of.get(practice.casefold(), practice),
                cost_rate=col("Cost per hour").lstrip("$"),
                effective_from=start,
                effective_to=_cell_date(row.get(columns["until"])) if "until" in columns else None,
                commit=False,
            )
        except RateCardError as e:
            errors.append(f"Row {n}: {e}")
    if errors:
        db.rollback()
        more = f" (and {len(errors) - 10} more)" if len(errors) > 10 else ""
        raise RateCardError("Nothing imported. " + " ".join(errors[:10]) + more)
    db.commit()
    return len(rows)


def export_csv(rows: list[RateCard]) -> str:
    """The same columns the upload reads, so an export is also the template."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(UPLOAD_COLUMNS)
    for r in rows:
        until = r.effective_to.isoformat() if r.effective_to else ""
        w.writerow([r.practice or "Any", r.grade, r.cost_rate, r.effective_from.isoformat(), until])
    return out.getvalue()
