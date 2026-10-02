"""Leadership and the GTD team admin: account overview. Fill speed, pipeline, and revenue lost
to missed start dates."""

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import EscalationStatus, Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import BusinessUnit, Demand, Escalation, OfferApproval
from app.services import chart_service, loss_service, reconcile_service
from app.services.demand_service import account_today, period_for

router = APIRouter(tags=["leadership"])
guard = require_screen("overview")


def _open_escalations(db: Session, account_id: int) -> dict[int, int]:
    rows = db.execute(
        select(Escalation.level, func.count())
        .where(Escalation.account_id == account_id, Escalation.status == EscalationStatus.OPEN.value)
        .group_by(Escalation.level)
    ).all()
    return {level: n for level, n in rows}


@router.get("/overview", response_class=HTMLResponse)
def overview_page(
    request: Request,
    start: str = "",
    end: str = "",
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    today = account_today(db, actor.account_id)
    o = loss_service.overview(db, actor.account_id, today, period_for(db, actor.account_id, start, end))
    # Offers this viewer decides: below the cut-off for leadership, at or above for the GTD team admin.
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
        nb=loss_service.non_billable_by_bu(db, actor.account_id),
        charts=_charts(o),
        sets_caps=actor.role is Role.ADMIN,
    )


def _money(v: Decimal) -> str:
    return f"${v:,.0f}"


def _short_money(v: Decimal) -> str:
    """For the middle of a donut, where a full figure doesn't fit: $80,856 → $81K."""
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    return f"${v / 1000:.0f}K" if v >= 10_000 else _money(v)


def _charts(o: loss_service.Overview) -> list[chart_service.Donut]:
    """The high-level picture: positions by stage, where the money is being lost, and the demand mix."""
    by_bu = sorted(o.by_bu, key=lambda s: s.name)  # a BU keeps its colour whatever its rank
    open_positions = sum(o.mix.values())
    return [
        chart_service.donut(
            "Positions by stage",
            "By main stage. Click a stage for its sub-stages.",
            [(label, n, str(n)) for _, label, n in o.pipeline],
            str(o.live),
            "positions",
            parts={label: [(name, str(c)) for name, c in o.subs.get(k, [])] for k, label, _ in o.pipeline},
        ),
        chart_service.donut(
            "Revenue lost by business unit",
            "Lost to date on positions unfilled past their start.",
            [(s.name, s.lost, _money(s.lost)) for s in by_bu],
            _short_money(o.lost_to_date),
            "lost to date",
        ),
        chart_service.donut(
            "Open positions by type",
            "New or replacement, billable or non-billable.",
            [(label, n, str(n)) for label, n in o.mix.items()],
            str(open_positions),
            "open positions",
        ),
    ]


@router.post("/overview/nb-caps")
async def save_nb_caps(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    """The GTD team admin sets the agreed number of non-billable positions per business unit."""
    if actor.role is not Role.ADMIN:
        return RedirectResponse("/overview?err=Only+the+GTD+team+admin+sets+the+caps#nb", status_code=303)
    form = await request.form()
    for bu in db.scalars(select(BusinessUnit).where(BusinessUnit.account_id == actor.account_id)):
        raw = str(form.get(f"cap_{bu.id}") or "").strip()
        if raw and (not raw.isdigit() or int(raw) > 500):
            return RedirectResponse(f"/overview?err={bu.name}:+enter+a+whole+number#nb", status_code=303)
        bu.nb_cap = int(raw) if raw else None
    db.commit()
    return RedirectResponse("/overview?msg=Non-billable+caps+saved#nb", status_code=303)


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
        "sub_stages": {key: dict(rows) for key, rows in o.subs.items()},
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
