"""Demands list: "My demands" for demand owners, "All demands" for full-account roles."""

from decimal import Decimal
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.db import get_db
from app.core.enums import DemandStatus, ResolutionAction, Role, Scope
from app.core.security import Actor, current_user, require_screen
from app.core.templating import render
from app.models import BusinessUnit, Demand, Escalation, User
from app.services import interview_service, loss_service, margin_service
from app.services.account_service import get_account
from app.services.demand_service import (
    FILTERS,
    account_today,
    can_change,
    can_edit,
    demand_rows,
    filter_counts,
    filter_rows,
    get_visible,
    stage_history,
    summary,
)
from app.services.escalation_service import current_doj
from app.services.margin_service import ApprovalError

router = APIRouter(tags=["demands"])
guard = require_screen("demands")


@router.get("/demands", response_class=HTMLResponse)
def demands_page(
    request: Request,
    filter: str = "all",
    bu: int | None = None,
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if filter not in FILTERS:
        filter = "all"
    all_rows = demand_rows(db, actor)
    # BU filter: only the BUs this actor can actually see, and only when there's more than one.
    bus_stmt = (
        select(BusinessUnit).where(BusinessUnit.account_id == actor.account_id).order_by(BusinessUnit.id)
    )
    if not actor.is_full:
        bus_stmt = bus_stmt.where(BusinessUnit.id.in_(actor.bu_ids))
    bus = list(db.scalars(bus_stmt)) if actor.scope in (Scope.FULL, Scope.OWN_BU_READ) else []
    if len(bus) < 2:
        bus = []
    return render(
        request,
        "my_demands/index.html",
        actor,
        db,
        rows=filter_rows(all_rows, filter, bu),
        counts=filter_counts(filter_rows(all_rows, "all", bu)),
        filters=FILTERS,
        active_filter=filter,
        bus=bus,
        active_bu=bu,
        stats=summary(all_rows),
        my_loss=_my_loss(db, actor),
        title="My demands" if actor.scope is not Scope.FULL else "All demands",
    )


@router.get("/api/demands")
def demands_json(
    filter: str = "all", bu: int | None = None, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = filter_rows(demand_rows(db, actor), filter if filter in FILTERS else "all", bu)
    out = []
    for r in rows:
        d = r.demand
        item: dict[str, Any] = {
            "app_ref": d.app_ref,
            "gtd_req_id": r.req_id,
            "name": d.name,
            "business_unit": d.business_unit.name,
            "practice": d.practice,
            "grade": d.grade,
            "start_date": d.start_date,
            "status": d.status,
            "status_label": r.status_label,
            "needs_attention": r.attention,
            "owner": d.owner.name,
            "read_only": r.read_only,
        }
        if actor.can_see_bill_rate:
            item["client_rate"] = d.client_rate
        out.append(item)
    return out


@router.get("/demands/{ref}", response_class=HTMLResponse)
def demand_page(
    ref: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> HTMLResponse:
    """One demand. Anyone who can see it may open it (interviewers included); actions follow edit rights."""
    demand = get_visible(db, actor, ref)
    if demand is None:
        raise HTTPException(404, "Demand not found, or not visible to you.")
    submitted_by = {s.submitted_by for s in demand.submissions}
    names: dict[int, str] = {
        uid: name for uid, name in db.execute(select(User.id, User.name).where(User.id.in_(submitted_by)))
    }
    return render(
        request,
        "my_demands/detail.html",
        actor,
        db,
        d=demand,
        can_change=can_change(actor, demand) and actor.role in (Role.DEMAND_OWNER, Role.ADMIN),
        can_submit=can_edit(actor, demand)
        and demand.status_enum in (DemandStatus.DRAFT, DemandStatus.RETURNED)
        and actor.role in (Role.DEMAND_OWNER, Role.ADMIN),
        history=stage_history(db, demand),
        escalations=list(
            db.scalars(
                select(Escalation)
                .where(Escalation.demand_id == demand.id)
                .order_by(Escalation.opened_at.desc())
            )
        ),
        sees_escalations=actor.role in (Role.ADMIN, Role.ADMIN_TEAM, Role.LEADERSHIP),
        candidates=interview_service.for_demand(db, demand.id),
        decides_rounds=demand.owner_id == actor.id or actor.role is Role.ADMIN,
        people={u.id: u.name for u in db.scalars(select(User).where(User.accounts.any(id=actor.account_id)))},
        submitters=names,
        statuses={s.value: s.label for s in DemandStatus},
        returned=_returned(db, demand),
        doj=current_doj(db, actor.account_id).get(demand.id),
        offers=margin_service.for_demand(db, demand.id),
        # The owner follows the money on their own demand; so do the roles that see rates.
        loss=_loss(db, actor, demand),
        askable=margin_service.askable(db, demand) if actor.id == demand.owner_id else [],
        channels={c.key: c.label for c in get_account(db, actor.account_id).settings.supply_channels},
    )


@router.post("/demands/{ref}/offers")
async def ask_for_offer_approval(
    ref: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    demand = get_visible(db, actor, ref)
    if demand is None:
        raise HTTPException(404, "Demand not found, or not visible to you.")
    f = await request.form()
    try:
        cid = int(str(f.get("candidate_id") or 0))
        margin_service.ask(db, actor, demand, cid, str(f.get("channel") or ""))
    except (ApprovalError, ValueError) as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}?err={quote(str(e))}#offers", status_code=303)
    msg = "Offer approval requested; you'll be emailed the decision"
    return RedirectResponse(f"/demands/{ref}?msg={quote(msg)}#offers", status_code=303)


@router.get("/demands/{ref}/jd")
def job_description(
    ref: str, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> FileResponse:
    demand = get_visible(db, actor, ref)
    if demand is None or not demand.jd_path:
        raise HTTPException(404, "No job description.")
    try:
        path = storage.open_path(demand.jd_path)
    except FileNotFoundError as e:
        raise HTTPException(404, "The job description file is missing.") from e
    return FileResponse(path, filename=path.name)


@router.post("/demands/{ref}/rounds/{interview_id}")
async def decide_round(
    ref: str,
    interview_id: int,
    request: Request,
    actor: Actor = Depends(current_user),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """The demand owner approves or declines a round the panelist asked for."""
    form = await request.form()
    try:
        iv = interview_service.decide_next_round(
            db, actor, interview_id, form.get("decision") == "approve", str(form.get("note") or "")
        )
    except interview_service.InterviewError as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}?err={quote(str(e))}#interviews", status_code=303)
    msg = f"{iv.round} approved: staffing schedules it" if iv.status == "open" else f"{iv.round} declined"
    return RedirectResponse(f"/demands/{ref}?msg={quote(msg)}#interviews", status_code=303)


def _returned(db: Session, demand: Demand) -> dict[str, Any] | None:
    """Why the demand was sent back: the escalation resolved with "send back for correction"."""
    esc = db.scalar(
        select(Escalation)
        .where(Escalation.demand_id == demand.id, Escalation.action == ResolutionAction.RETURN.value)
        .order_by(Escalation.resolved_at.desc())
        .limit(1)
    )
    if esc is None:
        return None
    who = db.get(User, esc.resolved_by) if esc.resolved_by else None
    return {
        "reason": esc.reason,
        "comment": esc.comment,
        "at": esc.resolved_at,
        "by": who.name if who else "the admin",
    }


def _loss(db: Session, actor: Actor, demand: Demand) -> loss_service.Loss | None:
    """Revenue lost on this demand so far, for its owner and for the roles that see rates."""
    if actor.id != demand.owner_id and not actor.can_see_bill_rate:
        return None
    today = account_today(db, actor.account_id)
    return next(
        (x for x in loss_service.losses(db, actor.account_id, today) if x.demand.id == demand.id), None
    )


def _my_loss(db: Session, actor: Actor) -> dict[str, Any] | None:
    """A demand owner's total: revenue lost so far on their own unfilled demands."""
    if actor.role is not Role.DEMAND_OWNER:
        return None
    today = account_today(db, actor.account_id)
    mine = [
        x
        for x in loss_service.losses(db, actor.account_id, today)
        if x.demand.owner_id == actor.id and not x.filled
    ]
    known = [x.lost for x in mine if x.lost is not None]
    return {"total": sum(known, Decimal(0)), "late": len(mine), "unknown": len(mine) - len(known)}
