"""Leadership and the admin demand owner: account overview. Fill speed, pipeline, and revenue lost
to missed start dates."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import EscalationStatus, Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import Demand, Escalation, OfferApproval
from app.services import loss_service, reconcile_service
from app.services.demand_service import account_today

router = APIRouter(tags=["leadership"])
guard = require_screen("overview")


def _open_escalations(db: Session, account_id: int) -> dict[int, int]:
    rows = db.execute(
        select(Escalation.level, func.count())
        .join(Demand)
        .where(Demand.account_id == account_id, Escalation.status == EscalationStatus.OPEN.value)
        .group_by(Escalation.level)
    ).all()
    return {level: n for level, n in rows}


@router.get("/overview", response_class=HTMLResponse)
def overview_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    today = account_today(db, actor.account_id)
    o = loss_service.overview(db, actor.account_id, today)
    # Offers this viewer decides: below the cut-off for leadership, at or above for the admin demand owner.
    route = "leadership" if actor.role is Role.LEADERSHIP else "admin"
    waiting = db.scalar(
        select(func.count())
        .select_from(OfferApproval)
        .join(Demand)
        .where(
            Demand.account_id == actor.account_id,
            OfferApproval.decision.is_(None),
            OfferApproval.route == route,
        )
    )
    return render(
        request,
        "leadership_dashboard/index.html",
        actor,
        db,
        o=o,
        latest=reconcile_service.latest_import(db, actor.account_id),
        escalations=_open_escalations(db, actor.account_id),
        offers_waiting=waiting or 0,
        max_group=max((n for _, _, n in o.pipeline), default=1) or 1,
    )


@router.get("/api/overview")
def overview_json(actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> dict[str, Any]:
    o = loss_service.overview(db, actor.account_id, account_today(db, actor.account_id))
    return {
        "as_of": o.today,
        "open": o.open,
        "need_coverage": o.need_coverage,
        "lost_to_date": o.lost_to_date,
        "projected_to_doj": o.projected,
        "missing_bill_rates": o.missing_rates,
        "pipeline": {key: n for key, _, n in o.pipeline},
        "at_risk": [
            {
                "app_ref": x.demand.app_ref,
                "gtd_req_id": x.demand.gtd_req_id,
                "start_date": x.demand.start_date,
                "doj": x.doj,
                "days_late": x.days_late,
                "working_days_late": x.working_days_late,
                "lost": x.lost,
            }
            for x in o.at_risk
        ],  # fmt: skip
    }
