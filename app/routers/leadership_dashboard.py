"""Leadership and the lead admin: account overview. Fill speed, pipeline, and revenue lost
to missed start dates."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import EscalationStatus, MainStage, Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import BusinessUnit, Demand, Escalation, OfferApproval
from app.services import costing_service, loss_service, reconcile_service, workflow_service
from app.services.account_service import get_account
from app.services.demand_service import account_today, period_for
from app.services.escalation_service import current_doj

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
    days: str = "",
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    today = account_today(db, actor.account_id)
    period = period_for(db, actor.account_id, start, end, days)
    o = loss_service.overview(db, actor.account_id, today, period)
    # Offers this viewer decides: below the cut-off for leadership, at or above for the lead admin.
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
        nb=loss_service.non_billable_by_bu(db, actor.account_id),
        ov=_page_data(db, o),
        sets_caps=actor.role is Role.ADMIN,
    )


# What each main stage means, in plain words, under its name on the overview.
MEANS = {
    MainStage.COVERAGE: (
        "The requisition has been raised and is progressing through GTD creation and approval, or suitable "
        "profiles are being sourced. No candidate has entered the selection process yet."
    ),
    MainStage.SELECTION: (
        "One or more candidates are under evaluation, either by the internal interview panel or by the "
        "client, and a selection decision is awaited."
    ),
    MainStage.ALLOC_PENDING: (
        "A candidate has been selected. The offer is awaiting commercial approval, or has been accepted "
        "and the candidate's date of joining is awaited."
    ),
    MainStage.ALLOC_DONE: "The selected candidate has joined and the position is fulfilled.",
    MainStage.ABANDONED: (
        "The requisition was cancelled in the BCM sheet, or closed by the demand owner or the GTD team, "
        "without a candidate being placed."
    ),
}


def _page_data(db: Session, o: loss_service.Overview) -> dict[str, Any]:
    """Everything static/overview.js needs: one row per position in view, and the order things show in."""
    account = get_account(db, o.demands[0].account_id) if o.demands else None
    doj = current_doj(db, account.id) if account else {}
    escs: dict[int, list[dict[str, Any]]] = {}
    if o.demands:
        for e in db.scalars(
            select(Escalation)
            .where(
                Escalation.demand_id.in_([d.id for d in o.demands]),
                Escalation.status == EscalationStatus.OPEN.value,
            )
            .order_by(Escalation.level.desc(), Escalation.opened_at)
        ):
            if e.demand_id is not None:
                escs.setdefault(e.demand_id, []).append({"t": e.type_enum.label, "l": e.level})
    costs = costing_service.costs(db, account.id, o.today) if account else {}
    rows = []
    for d in sorted(o.demands, key=lambda d: d.app_ref):
        loss = o.loss_of.get(d.id)
        cost = costs.get(d.id)
        kind = "Replacement" if d.type == "Replacement" else "New"
        rows.append(
            {
                "ref": d.app_ref,
                "req": d.gtd_req_id,
                "name": d.name,
                "stage": d.status_enum.main.label,
                "sub": d.status_enum.label,
                "bu": d.business_unit.name,
                "owner": d.owner.name,
                "practice": d.practice or "—",
                "type": f"{kind} · {'non-billable' if d.position_type == 'Non-billable' else 'billable'}",
                "start": f"{d.start_date:%d %b %Y}" if d.start_date else None,
                "joining": f"{doj[d.id]:%d %b %Y}" if doj.get(d.id) else None,
                "open": d.status_enum.main.value in loss_service.OPEN_GROUPS,
                "late": loss is not None and not loss.filled,
                "esc": escs.get(d.id, []),
                "costing": "Non-billable cost" if cost is not None else "",
                "cost": float(cost.so_far) if cost is not None and cost.so_far is not None else 0,
                "cost_month": float(cost.monthly) if cost is not None and cost.monthly is not None else 0,
                "cost_active": cost is not None and cost.active,
                "cost_rate": float(cost.rate) if cost is not None and cost.rate is not None else None,
                "cost_source": cost.source if cost is not None else None,
                "cost_days": cost.working_days if cost is not None else 0,
                "cost_until": f"{cost.until:%d %b %Y}" if cost is not None and cost.until else None,
                "resource": cost.resource if cost is not None else None,
                "grade": d.grade or "—",
                "escd": "Escalated" if d.id in escs else "",
                "pstart": "Past start" if loss is not None and not loss.filled else "",
                "days_late": loss.days_late if loss is not None and not loss.filled else 0,
                "lost": float(loss.lost) if loss is not None and loss.lost is not None else 0,
                "norate": loss is not None and loss.lost is None,
            }
        )
    return {
        "rows": rows,
        "order": {
            "stage": [m.label for m in MainStage],
            "bu": sorted({r["bu"] for r in rows}),
            "type": list(loss_service.MIX),
            "practice": list(account.settings.practices) if account else [],
        },
        "means": {m.label: text for m, text in MEANS.items()},
        "layout": workflow_service.layout(),
        "hours": f"{o.hours_per_day:g}",
        "month_days": costing_service.MONTH_DAYS,
        "caps": {
            b.name: b.nb_cap
            for b in db.scalars(select(BusinessUnit).where(BusinessUnit.account_id == account.id))
        }
        if account
        else {},
    }


@router.post("/overview/nb-caps")
async def save_nb_caps(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    """The lead admin sets the agreed number of non-billable positions per business unit."""
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
