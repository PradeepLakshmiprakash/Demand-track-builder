"""Housekeeping that runs once a day per account, and the renaming of a practice or a grade.

- figures: the day's headline numbers are stored, so what was reported on a day can be shown again;
- retention: when the account has set a retention time, candidates' personal details on long-finished
  demands are removed and old BCM sheet row snapshots are deleted. Off until the Administrator sets it;
- rename: a practice or grade is renamed once and every demand, rate, person and profile follows.
"""

import contextlib
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core import storage
from app.core.enums import EscalationStatus
from app.models import (
    Account,
    Candidate,
    CandidateExit,
    DailyFigure,
    Demand,
    Escalation,
    ExcelImport,
    ExcelRow,
    Interview,
    InterviewerProfile,
    UserAccount,
    UserPractice,
)
from app.services import costing_service, demand_service, loss_service, onboarding_service

log = logging.getLogger("demand_tracker.housekeeping")
REMOVED = "Removed (retention)"


class HousekeepingError(ValueError):
    pass


# --- Day-by-day figures -----------------------------------------------------------------------------


def capture_figures(db: Session, account: Account, today: date) -> DailyFigure:
    """Store today's headline figures, replacing an earlier capture of the same day."""
    o = loss_service.overview(db, account.id, today)
    costs = costing_service.costs(db, account.id, today)
    bills = onboarding_service.billing(db, account, o.demands, today)
    open_esc = db.scalar(
        select(func.count())
        .select_from(Escalation)
        .where(Escalation.account_id == account.id, Escalation.status == EscalationStatus.OPEN.value)
    )
    row = db.scalar(select(DailyFigure).where(DailyFigure.account_id == account.id, DailyFigure.day == today))
    if row is None:
        row = DailyFigure(account_id=account.id, day=today)
        db.add(row)
    row.open_positions = o.open
    row.late_positions = len(o.at_risk)
    row.revenue_lost = Decimal(o.lost_to_date or 0)
    row.nb_cost = sum((c.so_far or Decimal(0) for c in costs.values()), Decimal(0))
    row.unbilled = sum((b.amount or Decimal(0) for b in bills.values() if b.waiting), Decimal(0))
    row.escalations_open = open_esc or 0
    db.flush()
    return row


def trend(db: Session, account_id: int, days: int = 30) -> list[DailyFigure]:
    rows = db.scalars(
        select(DailyFigure)
        .where(DailyFigure.account_id == account_id)
        .order_by(DailyFigure.day.desc())
        .limit(days)
    )
    return list(reversed(list(rows)))


# --- Retention --------------------------------------------------------------------------------------


@dataclass
class Retained:
    candidates: int = 0
    sheet_rows: int = 0


def apply_retention(db: Session, account: Account, today: date) -> Retained:
    """Remove what the account no longer keeps. Counts and decisions stay; personal details go."""
    cfg = account.settings.retention
    out = Retained()
    if cfg.candidate_days:
        cutoff = today - timedelta(days=cfg.candidate_days)
        demands = list(db.scalars(select(Demand).where(Demand.account_id == account.id)))
        finished = demand_service.finished_dates(db, demands)
        old = [i for i, day in finished.items() if day <= cutoff]
        if old:
            cands = db.scalars(
                select(Candidate).where(Candidate.demand_id.in_(old), Candidate.name != REMOVED)
            )
            for c in cands:
                if c.cv_path:
                    with contextlib.suppress(FileNotFoundError, NotImplementedError):
                        storage.open_path(c.cv_path).unlink()
                c.name, c.cv_path = REMOVED, None
                for iv in db.scalars(select(Interview).where(Interview.candidate_id == c.id)):
                    iv.comments, iv.feedback_details = None, {}
                for x in db.scalars(select(CandidateExit).where(CandidateExit.candidate_id == c.id)):
                    x.candidate_name = REMOVED
                out.candidates += 1
    if cfg.sheet_imports_kept:
        keep = list(
            db.scalars(
                select(ExcelImport.id)
                .where(ExcelImport.account_id == account.id)
                .order_by(ExcelImport.imported_at.desc(), ExcelImport.id.desc())
                .limit(cfg.sheet_imports_kept)
            )
        )
        older = select(ExcelImport.id).where(
            ExcelImport.account_id == account.id, ExcelImport.id.notin_(keep)
        )
        done = db.execute(delete(ExcelRow).where(ExcelRow.import_id.in_(older)))
        out.sheet_rows = getattr(done, "rowcount", 0) or 0
    db.flush()
    return out


def run_daily(db: Session, account: Account) -> None:
    today = demand_service.account_today(db, account.id)
    capture_figures(db, account, today)
    kept = apply_retention(db, account, today)
    if kept.candidates or kept.sheet_rows:
        log.info("retention %s: %s candidates, %s sheet rows", account.name, kept.candidates, kept.sheet_rows)
    db.commit()


# --- Renaming a practice or a grade -----------------------------------------------------------------


def rename(db: Session, account_id: int, kind: str, old: str, new: str) -> int:
    """Rename a practice or a grade everywhere. Returns how many demands carry it."""
    if kind not in ("practice", "grade"):
        raise HousekeepingError("Rename a practice or a grade.")
    old, new = old.strip(), " ".join(new.split())
    limit = 40 if kind == "practice" else 10
    if not new or len(new) > limit:
        raise HousekeepingError(f"The new name is 1 to {limit} characters.")
    acc = db.get_one(Account, account_id)
    cfg = acc.settings
    names = cfg.practices if kind == "practice" else cfg.grades
    if old not in names:
        raise HousekeepingError(f"{old} isn't on the list.")
    if new == old:
        raise HousekeepingError("That is already its name.")
    if new.casefold() in {n.casefold() for n in names if n != old}:
        raise HousekeepingError(f"{new} is already on the list.")
    table = "practices" if kind == "practice" else "grades"
    taken = db.scalar(
        text(f"SELECT 1 FROM {table} WHERE account_id = :a AND name = :n"), {"a": account_id, "n": new}
    )
    if taken:
        raise HousekeepingError(f"{new} was used before in this account; pick another name.")
    column = Demand.practice if kind == "practice" else Demand.grade
    used = db.scalar(
        select(func.count()).select_from(Demand).where(Demand.account_id == account_id, column == old)
    )
    # one update here; the foreign keys carry it to every demand and rate
    db.execute(
        text(f"UPDATE {table} SET name = :new WHERE account_id = :a AND name = :old"),
        {"a": account_id, "old": old, "new": new},
    )
    members = select(UserAccount.user_id).where(UserAccount.account_id == account_id)
    if kind == "practice":
        cfg.practices = [new if n == old else n for n in cfg.practices]
        cfg.practice_stacks = {(new if k == old else k): v for k, v in cfg.practice_stacks.items()}
        for link in db.scalars(
            select(UserPractice).where(UserPractice.user_id.in_(members), UserPractice.practice == old)
        ):
            link.practice = new
        for prof in db.scalars(select(InterviewerProfile).where(InterviewerProfile.user_id.in_(members))):
            if old in (prof.practices or []):
                prof.practices = [new if p == old else p for p in prof.practices]
    else:
        cfg.grades = [new if n == old else n for n in cfg.grades]
        for prof in db.scalars(select(InterviewerProfile).where(InterviewerProfile.user_id.in_(members))):
            if prof.max_grade == old:
                prof.max_grade = new
    acc.settings = cfg
    db.commit()
    db.expire_all()  # demands and rates changed underneath the session
    return used or 0
