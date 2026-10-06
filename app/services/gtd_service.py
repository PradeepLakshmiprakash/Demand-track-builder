"""GTD queue: demands waiting to be entered on GTD, and linking the requisition ID GTD generates.

The app has no view into GTD. The lead admin or the GTD admin team enters each demand there by
hand (the plain demand name) and pastes the requisition ID back. That link is the key the
BCM sheet reconciles on, so an ID can be linked only once, ever.
"""

import csv
import io
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.core.enums import DemandStatus
from app.core.security import Actor
from app.models import Account, Demand, GtdSubmission, NotificationBatch
from app.services.demand_service import record_stage
from app.services.notify_service import local_now, safely, send_created

REQ_ID = re.compile(r"^[A-Z0-9]{4,12}$")
PENDING = (DemandStatus.SUBMITTED, DemandStatus.NOTIFIED)


class GtdError(ValueError):
    pass


def normalize_req_id(raw: str) -> str:
    return re.sub(r"\s+", "", raw or "").upper()


@dataclass
class Queue:
    in_mail: list[Demand]  # notified: in an admin mail, waiting for GTD entry
    next_mail: list[Demand]  # submitted: goes out in the next mail (can be entered early)
    linked_today: list[Demand]
    last_batch: NotificationBatch | None


def queue(db: Session, account_id: int) -> Queue:
    opts = (selectinload(Demand.business_unit), selectinload(Demand.owner), selectinload(Demand.submissions))
    pending = list(
        db.scalars(
            select(Demand)
            .where(Demand.account_id == account_id, Demand.status.in_([s.value for s in PENDING]))
            .options(*opts)
            .order_by(Demand.submitted_at, Demand.app_ref)
        )
    )
    account = db.get_one(Account, account_id)
    since = local_now(account).replace(hour=0, minute=0, second=0, microsecond=0)
    linked = list(
        db.scalars(
            select(Demand)
            .join(GtdSubmission, GtdSubmission.demand_id == Demand.id)
            .where(Demand.account_id == account_id, GtdSubmission.submitted_at >= since)
            .options(*opts)
            .order_by(GtdSubmission.submitted_at.desc())
        ).unique()
    )
    last = db.scalar(
        select(NotificationBatch)
        .where(NotificationBatch.account_id == account_id)
        .order_by(NotificationBatch.sent_at.desc())
        .limit(1)
    )
    return Queue(
        in_mail=[d for d in pending if d.status == DemandStatus.NOTIFIED.value],
        next_mail=[d for d in pending if d.status == DemandStatus.SUBMITTED.value],
        linked_today=linked,
        last_batch=last,
    )


def link_requisition(db: Session, actor: Actor, demand_id: int, raw_req_id: str) -> GtdSubmission:
    req_id = normalize_req_id(raw_req_id)
    if not REQ_ID.match(req_id):
        raise GtdError(
            f"'{raw_req_id.strip()}' doesn't look like a GTD requisition ID (4-12 letters or digits)."
        )
    demand = db.scalar(select(Demand).where(Demand.id == demand_id, Demand.account_id == actor.account_id))
    if demand is None:
        raise GtdError("Demand not found.")
    if demand.status_enum not in PENDING:
        raise GtdError(
            f"{demand.app_ref} is {demand.status_enum.label.lower()}; only submitted demands can be linked."
        )
    taken = db.scalar(select(Demand.app_ref).join(GtdSubmission).where(GtdSubmission.gtd_req_id == req_id))
    if taken:
        raise GtdError(f"{req_id} is already linked to {taken}. A requisition ID can only be linked once.")

    # A resubmitted demand keeps its history: the new ID chains to the one it replaces.
    previous = db.scalar(
        select(GtdSubmission.id)
        .where(GtdSubmission.demand_id == demand.id)
        .order_by(GtdSubmission.submitted_at.desc(), GtdSubmission.id.desc())
        .limit(1)
    )
    sub = GtdSubmission(
        demand_id=demand.id, gtd_req_id=req_id, submitted_by=actor.id, previous_submission_id=previous
    )
    db.add(sub)
    record_stage(db, demand, DemandStatus.SENT_TO_GTD, actor.id)
    try:
        db.commit()
    except IntegrityError as e:  # two people linking the same ID at once
        db.rollback()
        raise GtdError(f"{req_id} was just linked to another demand.") from e
    safely(send_created, db, demand, req_id, actor.name)
    return sub


EXPORT_COLUMNS = [
    "Demand request name", "App ref", "Business unit", "Practice", "Grade", "Category", "Type",
    "Replacing", "Position type", "Primary skills", "Secondary skills", "Experience (yrs)",
    "Client bill rate", "Requested start date", "Region", "Location", "Work mode", "Hiring manager",
    "Demand owner",
]  # fmt: skip


def export_csv(demands: list[Demand]) -> str:
    """What the admin needs to key each demand into GTD. The name is the plain demand name."""
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(EXPORT_COLUMNS)
    for d in demands:
        exp = f"{d.exp_min or ''}-{d.exp_max or ''}" if d.exp_min is not None or d.exp_max is not None else ""
        w.writerow([
            d.gtd_name, d.app_ref, d.business_unit.name, d.practice, d.grade, d.category, d.type,
            d.replaced_resource or "", d.position_type, ", ".join(d.primary_skills),
            ", ".join(d.secondary_skills), exp, d.client_rate if d.client_rate is not None else "",
            d.start_date.isoformat() if d.start_date else "", d.region, d.location, d.work_mode,
            d.hiring_manager or "", d.owner.name,
        ])  # fmt: skip
    return out.getvalue()
