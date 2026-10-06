"""python -m seed.flavors : add sample demands so every step of the workflow has at least one, each with a
dated history, and give the seeded demands a history too (so a demand's workflow shows when it reached
each step). Adds to what is there; running it twice adds nothing the second time. Placeholder data only.
"""

import secrets
import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.account_config import DEFAULT_RULES
from app.core.db import new_session
from app.core.enums import DemandStatus
from app.models import (
    Account,
    BusinessUnit,
    Candidate,
    Demand,
    Escalation,
    EscalationEvent,
    GtdSubmission,
    Interview,
    StageEvent,
    User,
)
from app.services.workflow_service import PATH, PROBLEMS
from seed import data

S = DemandStatus
ORDER = [s for path in PATH.values() for s in path]
HANGS = {s: PATH[next(iter(PATH))][row] for s, row in PROBLEMS}

# status, owner, BU, name, practice, grade, start in days from today, requisition ID, extra fields
FLAVORS: list[tuple[DemandStatus, str, str, str, str, str, int, str | None, dict[str, Any]]] = [
    (S.RETURNED, "Priya N.", "PAYMENTS", "Payments API Developer (Kafka)", "CCA-FS", "C1", 35, None, {}),
    (S.SENT_TO_GTD, "Rahul K.", "CARDS", "Cards Fraud Analytics Engineer", "DMN-FS", "C2", 40, "K4RD7X", {}),
    (S.LINKED, "Meera S.", "BANKING", "Banking Core QA Automation", "TES-FS", "C1", 30, "L8NK3D", {}),
    (S.DROPPED, "Arjun D.", "DATA", "Data Platform DevOps Engineer", "DMN-FS", "C2", 25, "DR0P5Q", {}),
    (S.PANEL_SELECTED, "Priya N.", "PAYMENTS", "Senior React Developer Payments Portal", "DCX-FS", "C2", 20,
     "PN5L3C", {}),
    (S.CLOSED, "Rahul K.", "CARDS", "Cards Rewards Test Analyst", "TES-FS", "B1", 15, "CL0S3D", {}),
]  # fmt: skip


def _trail(status: DemandStatus) -> list[DemandStatus]:
    """The steps a demand passed to be where it is: the normal path up to its step, then the exception."""
    if status in ORDER:
        return ORDER[: ORDER.index(status) + 1]
    if status in HANGS:
        return [*ORDER[: ORDER.index(HANGS[status]) + 1], status]
    return [*ORDER[: ORDER.index(S.COVERAGE_REQUIRED) + 1], status]  # cancelled, closed


def _history(db: Session, demand: Demand, last: datetime, actor_id: int) -> None:
    trail = _trail(demand.status_enum)
    gaps = [2, 1, 3, 2, 4, 5, 3, 4, 6, 5, 4]
    at = last
    events = []
    for i in range(len(trail) - 1, -1, -1):
        events.append((trail[i - 1].value if i else None, trail[i].value, at))
        at -= timedelta(days=gaps[i % len(gaps)])
    for frm, to, when in reversed(events):
        db.add(
            StageEvent(
                demand_id=demand.id, from_stage=frm, to_stage=to, origin="app", actor_id=actor_id, at=when
            )
        )


def add_flavors(
    db: Session, account_name: str = "Discover NA", now: datetime | None = None
) -> tuple[int, int]:
    now = now or datetime.now(UTC)
    account = db.scalars(select(Account).where(Account.name == account_name)).one()
    users = {u.name: u for u in db.scalars(select(User))}
    bus = {b.name: b for b in db.scalars(select(BusinessUnit).where(BusinessUnit.account_id == account.id))}
    admin = users.get("Kavya R.") or next(iter(users.values()))

    # a history for demands that only carry the single event they were seeded with
    backfilled = 0
    for d in db.scalars(select(Demand).where(Demand.account_id == account.id)):
        events = list(db.scalars(select(StageEvent).where(StageEvent.demand_id == d.id)))
        if len(events) == 1 and len(_trail(d.status_enum)) > 1:
            last = events[0].at
            db.delete(events[0])
            db.flush()
            _history(db, d, last, d.owner_id)
            backfilled += 1

    added = 0
    for status, owner, bu, name, practice, grade, start_in, req, extra in FLAVORS:
        if db.scalar(
            select(func.count())
            .select_from(Demand)
            .where(Demand.account_id == account.id, Demand.name == name)
        ):
            continue
        if req and db.scalar(
            select(func.count()).select_from(GtdSubmission).where(GtdSubmission.gtd_req_id == req)
        ):
            continue
        fields: dict[str, Any] = {**data.DEFAULTS, **extra}
        fields["client_rate"] = Decimal(str(fields["client_rate"]))
        d = Demand(
            account_id=account.id, bu_id=bus[bu].id, owner_id=users[owner].id, name=name, practice=practice,
            grade=grade, start_date=date.today() + timedelta(days=start_in), status=status.value,
            submitted_at=now - timedelta(days=12), primary_skills=["Java"], **fields,
        )  # fmt: skip
        db.add(d)
        db.flush()
        # how long ago it reached where it is: recent enough that nothing new escalates by itself
        _history(db, d, now - timedelta(days=1 if status is S.SENT_TO_GTD else 2), users[owner].id)
        if req:
            sent = now - timedelta(days=1 if status is S.SENT_TO_GTD else 8)
            db.add(GtdSubmission(demand_id=d.id, gtd_req_id=req, submitted_by=admin.id, submitted_at=sent))
        if status is S.DROPPED:
            due = now + timedelta(days=1)
            esc = Escalation(
                account_id=account.id, demand_id=d.id, type="dropped", level=1, status="open",
                detail="In the previous BCM sheet, gone from the latest", opened_at=now - timedelta(days=1),
                due_at=due, notified_level=1, severity=DEFAULT_RULES["dropped"][1].value,
                responsible=DEFAULT_RULES["dropped"][0].value,
            )  # fmt: skip
            db.add(esc)
            db.flush()
            db.add(
                EscalationEvent(
                    escalation_id=esc.id, kind="opened", level=1, at=esc.opened_at, note=esc.detail
                )
            )
        if status is S.PANEL_SELECTED:
            c = Candidate(account_id=account.id, demand_id=d.id, name="Candidate S", channel="fte",
                          current_stage="Internal panel")  # fmt: skip
            db.add(c)
            db.flush()
            panel = users.get("Vikram P.")
            db.add(
                Interview(
                    demand_id=d.id, candidate_id=c.id, round="L1", status="completed",
                    interviewer_id=panel.id if panel else None, scheduled_at=now - timedelta(days=3),
                    feedback_token=secrets.token_urlsafe(32), ratings={}, outcome="select",
                    comments="Strong React and TypeScript; ready for the client round.",
                    submitted_at=now - timedelta(days=2),
                )
            )  # fmt: skip
        added += 1
    db.commit()
    return added, backfilled


if __name__ == "__main__":
    with new_session() as session:
        n, b = add_flavors(session, sys.argv[1] if len(sys.argv) > 1 else "Discover NA")
    print(f"Added {n} demands; gave {b} seeded demands a dated history.")
