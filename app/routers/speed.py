"""Speed report: time to fill, where the time goes, who did not join, and the wait for billing."""

import csv
import io

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import DemandStatus, Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import BusinessUnit, User, member_of
from app.services import housekeeping_service, speed_service
from app.services.account_service import get_account
from app.services.demand_service import account_today

router = APIRouter(tags=["speed report"])
guard = require_screen("speed")


def _report(
    db: Session, actor: Actor, days: int, bu: str, practice: str, owner: str, by: str
) -> speed_service.Report:
    account = get_account(db, actor.account_id)
    days = days if days in speed_service.PERIODS else 90
    by = by if by in speed_service.GROUPS else "bu"
    return speed_service.report(
        db, account, account_today(db, account.id), days=days, bu=bu, practice=practice, owner=owner, by=by
    )


@router.get("/speed", response_class=HTMLResponse)
def speed_page(
    request: Request,
    days: int = 90,
    bu: str = "",
    practice: str = "",
    owner: str = "",
    by: str = "bu",
    step: str = "",
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    account = get_account(db, actor.account_id)
    r = _report(db, actor, days, bu, practice, owner, by)
    labels = {s.value: s.label for s in DemandStatus}
    return render(
        request,
        "speed/index.html",
        actor,
        db,
        r=r,
        days=days if days in speed_service.PERIODS else 90,
        periods=speed_service.PERIODS,
        bu=bu,
        practice=practice,
        owner=owner,
        by=by if by in speed_service.GROUPS else "bu",
        groups=speed_service.GROUPS,
        step=step if step in r.stays else "",
        step_label=labels.get(step, ""),
        bus=list(
            db.scalars(
                select(BusinessUnit.name)
                .where(BusinessUnit.account_id == account.id)
                .order_by(BusinessUnit.name)
            )
        ),
        practices=account.settings.practices,
        owners=list(
            db.scalars(
                select(User).where(member_of(account.id, Role.DEMAND_OWNER, Role.ADMIN)).order_by(User.name)
            )
        ),
        query=request.url.query,
        trend=housekeeping_service.trend(db, account.id, 30),
    )


@router.get("/speed.csv")
def speed_csv(
    days: int = 90,
    bu: str = "",
    practice: str = "",
    owner: str = "",
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> Response:
    """Every stay at every step in the period, one row each, plus a row per position filled."""
    r = _report(db, actor, days, bu, practice, owner, "bu")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(
        [
            "Demand",
            "Requisition",
            "Name",
            "Demand owner",
            "Business unit",
            "Practice",
            "Measure",
            "From",
            "To",
            "Working days",
        ]
    )
    labels = {s.value: s.label for s in DemandStatus}
    for key, stays in r.stays.items():
        for s in stays:
            d = s.demand
            w.writerow([d.app_ref, d.gtd_req_id or "", d.name, d.owner.name, d.business_unit.name,
                        d.practice or "",
                        labels[key], s.entered.isoformat(), s.left.isoformat(), s.days])  # fmt: skip
    for d, n, late in r.fills:
        w.writerow([d.app_ref, d.gtd_req_id or "", d.name, d.owner.name, d.business_unit.name,
                        d.practice or "",
                    "Time to fill (late)" if late else "Time to fill", "", "", n])  # fmt: skip
    name = f"speed-report-{r.since:%Y%m%d}-{r.today:%Y%m%d}.csv"
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
