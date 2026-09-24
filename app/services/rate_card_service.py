"""Vendor rate card (flow-artifact §8.1): cost per hour by grade, practice, region and supply channel.

Rates are dated and never edited in place, so an old offer's margin can always be recomputed with the
rate that applied then. Adding a rate for a key that already has one open-ended row closes that row the
day before the new one starts. A blank practice means "any practice"; a practice-specific rate wins.
"""

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

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
    commit: bool = True,
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
    return list(
        db.scalars(
            stmt.order_by(
                RateCard.channel, RateCard.region, RateCard.grade, RateCard.practice, RateCard.effective_from
            )
        )
    )


# --- Bulk upload -----------------------------------------------------------------------------------

UPLOAD_COLUMNS = ["Channel", "Region", "Grade", "Practice", "Cost per hour", "From", "Until"]


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
    channel_of = {c.key.casefold(): c.key for c in cfg.supply_channels}
    channel_of |= {c.label.casefold(): c.key for c in cfg.supply_channels}
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
                practice=None if practice.casefold() in ("", "any") else practice,
                region=col("Region").upper(),
                channel=channel_of.get(col("Channel").casefold(), ""),
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


def export_csv(rows: list[RateCard], channel_labels: dict[str, str]) -> str:
    """The same columns the upload reads, so an export is also the template."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(UPLOAD_COLUMNS)
    for r in rows:
        until = r.effective_to.isoformat() if r.effective_to else ""
        label = channel_labels.get(r.channel, r.channel)
        w.writerow(
            [label, r.region, r.grade, r.practice or "Any", r.cost_rate, r.effective_from.isoformat(), until]
        )
    return out.getvalue()
