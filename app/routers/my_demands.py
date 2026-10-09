"""Demands list: "My demands" for demand owners, "All demands" for full-account roles."""

from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail, storage
from app.core.config import get_settings
from app.core.db import get_db
from app.core.enums import DemandStatus, ResolutionAction, Role, Scope
from app.core.security import Actor, current_user, require_screen
from app.core.templating import _account_tz, render
from app.models import BusinessUnit, Demand, Escalation, User
from app.services import (
    costing_service,
    demand_service,
    escalation_service,
    interview_service,
    loss_service,
    margin_service,
    notify_service,
    onboarding_service,
    pipeline_service,
    workflow_service,
)
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
    period_for,
    stage_history,
    summary,
)
from app.services.escalation_service import current_doj
from app.services.margin_service import ApprovalError

PAGE_SIZE = 50  # demands shown at a time in the list

router = APIRouter(tags=["demands"])
guard = require_screen("demands")


@router.get("/demands", response_class=HTMLResponse)
def demands_page(
    request: Request,
    filter: str = "all",
    bu: int | None = None,
    start: str = "",
    end: str = "",
    days: str = "",
    page: int = 1,
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if filter not in FILTERS:
        filter = "all"
    period = period_for(db, actor.account_id, start, end, days)
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
    shown = filter_rows(all_rows, filter, bu, period)
    pages = max(1, -(-len(shown) // PAGE_SIZE))
    page = min(max(page, 1), pages)
    return render(
        request,
        "my_demands/index.html",
        actor,
        db,
        rows=shown[(page - 1) * PAGE_SIZE : page * PAGE_SIZE],
        total=len(shown),
        page=page,
        pages=pages,
        page_query=urlencode([(k, v) for k, v in request.query_params.multi_items() if k != "page"]),
        counts=filter_counts(filter_rows(all_rows, "all", bu), period),
        period=period,
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
    filter: str = "all",
    bu: int | None = None,
    start: str = "",
    end: str = "",
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    period = period_for(db, actor.account_id, start, end)
    rows = filter_rows(demand_rows(db, actor), filter if filter in FILTERS else "all", bu, period)
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
            "joining_date": r.doj,
            "status": d.status,
            "main_stage": r.main.label,
            "status_label": r.status_label,
            "needs_attention": r.attention,
            "owner": d.owner.name,
            "read_only": r.read_only,
        }
        if actor.sees_rates(d.owner_id):
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
    account = get_account(db, demand.account_id)
    ob = account.settings.onboarding
    today = account_today(db, demand.account_id)
    if demand.status_enum is DemandStatus.OFFER_IN_MARKET and not onboarding_service.items(db, demand.id):
        onboarding_service.ensure_checklist(db, account, demand)  # the offer is made: its checklist starts
        db.commit()
    check = onboarding_service.checklist(db, actor, demand, today)
    return render(
        request,
        "my_demands/detail.html",
        actor,
        db,
        d=demand,
        can_change=can_change(actor, demand) and actor.role in (Role.DEMAND_OWNER, Role.ADMIN),
        can_submit=can_edit(actor, demand)
        and (demand.status_enum is DemandStatus.DRAFT or demand.status_enum in demand_service.RESUBMITTABLE)
        and actor.role in (Role.DEMAND_OWNER, Role.ADMIN),
        cfg=account.settings,
        can_set_doj=demand_service.can_set_joining(actor, demand),
        editing=request.query_params.get("edit", ""),
        history=stage_history(db, demand),
        escalations=list(
            db.scalars(
                select(Escalation)
                .where(Escalation.demand_id == demand.id)
                .order_by(Escalation.opened_at.desc())
            )
        ),
        sees_escalations=actor.role in (Role.ADMIN, Role.ADMIN_TEAM, Role.LEADERSHIP),
        respond=_to_respond(db, actor, demand),
        wf_layout=workflow_service.layout(),
        wf_at=workflow_service.position(demand),
        wf_chips=(
            {demand.status_enum.label: f"{check.done} of {check.total} pre-joining"}
            if check and demand.status_enum is DemandStatus.OFFER_IN_MARKET
            else {}
        ),
        checklist=check,
        replaces=db.get(Demand, demand.replaces_demand_id) if demand.replaces_demand_id else None,
        replaced_by=db.scalars(select(Demand).where(Demand.replaces_demand_id == demand.id)).first(),
        can_link_replaced=demand_service.can_link_replaced(actor, demand),
        replaceable=demand_service.replaceable(db, demand)
        if demand_service.can_link_replaced(actor, demand)
        else [],
        exits=onboarding_service.exits_for(db, demand.id),
        exit_kinds=onboarding_service.EXIT_KINDS,
        can_record_exit=onboarding_service.can_record_exit(actor, demand),
        exit_candidates=onboarding_service.exit_candidates(db, demand),
        ob=ob,
        billing=onboarding_service.billing(db, account, [demand], today).get(demand.id),
        can_set_billing=onboarding_service.can_set_billing(actor, demand),
        wf_next=workflow_service.next_step(demand),
        wf_reached=workflow_service.reached(demand, stage_history(db, demand), _account_tz(db, actor)),
        wf_esc=[
            {"t": e.type_enum.label, "l": e.level}
            for e in db.scalars(
                select(Escalation)
                .where(Escalation.demand_id == demand.id, Escalation.status == "open")
                .order_by(Escalation.level.desc(), Escalation.opened_at)
            )
        ],
        can_revise_dates=False,  # the whole demand is editable now: dates are changed with Edit
        can_close=demand_service.can_change(actor, demand) and demand.status != "staffed",
        close_reasons=account.settings.resolution_reasons,
        cost=costing_service.costs(db, demand.account_id).get(demand.id) if demand.is_proactive_nb else None,
        can_mark_billable=costing_service.can_mark_billable(actor, demand),
        billable_by=db.get(User, demand.billable_marked_by) if demand.billable_marked_by else None,
        today=today,
        candidates=interview_service.for_demand(db, demand.id),
        decides_rounds=demand.owner_id == actor.id,  # an extra round is the demand owner's call
        people={u.id: u.name for u in db.scalars(select(User).where(User.accounts.any(id=actor.account_id)))},
        submitters=names,
        statuses={s.value: s.full for s in DemandStatus},
        returned=_returned(db, demand),
        doj=current_doj(db, actor.account_id).get(demand.id),
        offers=margin_service.for_demand(db, demand.id),
        sees_rates=actor.sees_rates(demand.owner_id),
        can_set_joining=margin_service.can_set_joining(db, actor, demand),
        doj_from_sheet=_sheet_doj(db, demand),
        # The owner follows the money on their own demand; so do the roles that see rates.
        loss=_loss(db, actor, demand),
        askable=margin_service.askable(db, demand) if actor.id == demand.owner_id else [],
        client_pending=(
            pipeline_service.awaiting_client(db, demand)
            if pipeline_service.can_record_client(actor, demand) and demand.client_interview_required
            else []
        ),
    )


@router.post("/demands/{ref}/joining-date")
async def record_joining_date(
    ref: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    """Offer accepted: the demand owner records the expected date of joining."""
    demand = get_visible(db, actor, ref)
    if demand is None:
        raise HTTPException(404, "Demand not found, or not visible to you.")
    raw = str((await request.form()).get("expected_doj") or "")
    try:
        margin_service.set_joining_date(db, actor, demand, date.fromisoformat(raw) if raw else None)
    except (ApprovalError, ValueError) as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}?err={quote(str(e))}#offers", status_code=303)
    return RedirectResponse(f"/demands/{ref}?msg=Joining+date+recorded#offers", status_code=303)


@router.post("/demands/{ref}/dates")
async def revise_dates(
    ref: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    """Start date and last working day stay editable after the demand is on GTD."""
    demand = get_visible(db, actor, ref)
    if demand is None:
        raise HTTPException(404, "Demand not found, or not visible to you.")
    f = await request.form()

    def day(key: str) -> date | None:
        raw = str(f.get(key) or "")
        return date.fromisoformat(raw) if raw else None

    try:
        changes = demand_service.revise_dates(db, actor, demand, day("start_date"), day("lwd"))
    except ValueError as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}?err={quote(str(e))}#dates", status_code=303)
    if not changes:
        return RedirectResponse(f"/demands/{ref}?msg=No+change#dates", status_code=303)
    closed = escalation_service.close_past_start_on_new_date(db, actor, demand)
    tag = f"{demand.gtd_req_id} | {demand.app_ref}" if demand.gtd_req_id else demand.app_ref
    owner = db.get_one(User, demand.owner_id)
    notify_service.safely(
        mail.send,
        mail.Mail(
            to=margin_service._team_admins(db, demand.account_id),
            cc=sorted({actor.email, owner.email}),
            subject=f"[{tag}] Dates changed by {actor.name}",
            text=(
                f"{actor.name} changed the dates on {demand.app_ref} ({demand.name}):\n"
                + "\n".join(f"- {c}" for c in changes)
                + "\nUpdate GTD if the requisition carries these dates.\n\n"
                f"{get_settings().app_base_url}/demands/{demand.app_ref}"
            ),
        ),
    )
    db.commit()
    msg = "Dates updated; the past start date escalation is closed" if closed else "Dates updated"
    return RedirectResponse(f"/demands/{ref}?msg={quote(msg)}#dates", status_code=303)


def _own_demand(db: Session, actor: Actor, ref: str) -> Demand:
    demand = get_visible(db, actor, ref)
    if demand is None:
        raise HTTPException(404, "Demand not found, or not visible to you.")
    return demand


@router.post("/demands/{ref}/close")
async def close_demand(
    ref: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    """The position is no longer needed: its owner closes the demand, with the reason."""
    demand = _own_demand(db, actor, ref)
    f = await request.form()
    try:
        demand_service.close_demand(db, actor, demand, str(f.get("reason") or ""))
    except ValueError as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}?err={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/demands/{ref}?msg=Demand+closed", status_code=303)


@router.post("/demands/{ref}/replaces")
async def link_replaced(
    ref: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    """This demand re-raises a position whose earlier demand was abandoned."""
    demand = _own_demand(db, actor, ref)
    f = await request.form()
    try:
        other = demand_service.set_replaces(db, actor, demand, str(f.get("replaces") or ""))
    except ValueError as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}?err={quote(str(e))}", status_code=303)
    msg = f"Linked: this demand re-raises {other.app_ref}" if other else "Link removed"
    return RedirectResponse(f"/demands/{ref}?msg={quote(msg)}", status_code=303)


@router.post("/demands/{ref}/checklist/{item_id}")
async def update_checklist_item(
    ref: str,
    item_id: int,
    request: Request,
    actor: Actor = Depends(current_user),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    """A pre-joining item: done, in progress, blocked (with what it waits on) or back to not started."""
    demand = _own_demand(db, actor, ref)
    f = await request.form()
    try:
        onboarding_service.update_item(
            db, actor, demand, item_id, str(f.get("status") or ""), str(f.get("note") or "")
        )
    except onboarding_service.OnboardingError as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}?err={quote(str(e))}#checklist", status_code=303)
    return RedirectResponse(f"/demands/{ref}?msg=Checklist+updated#checklist", status_code=303)


@router.post("/demands/{ref}/no-join")
async def record_no_join(
    ref: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    """The candidate with the offer will not join: record why; the demand goes back to sourcing."""
    demand = _own_demand(db, actor, ref)
    f = await request.form()
    raw_id, raw_day = str(f.get("candidate_id") or ""), str(f.get("on_date") or "")
    try:
        on = date.fromisoformat(raw_day)
        out = onboarding_service.record_exit(
            db, actor, demand, int(raw_id) if raw_id.isdigit() else None,
            str(f.get("kind") or ""), str(f.get("reason") or ""), on,
        )  # fmt: skip
    except ValueError as e:
        db.rollback()
        text = str(e) if isinstance(e, onboarding_service.OnboardingError) else "Enter the date it happened."
        return RedirectResponse(f"/demands/{ref}?err={quote(text)}#no-join", status_code=303)
    msg = f"Recorded: {out.candidate_name} will not join. The demand is back at Sourcing profiles"
    return RedirectResponse(f"/demands/{ref}?msg={quote(msg)}", status_code=303)


@router.post("/demands/{ref}/billing")
async def record_billing(
    ref: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    """A joined, billable position: its first billable day, or why billing has not started."""
    demand = _own_demand(db, actor, ref)
    f = await request.form()
    raw = str(f.get("billable_from") or "")
    try:
        started = date.fromisoformat(raw) if raw and not f.get("waiting") else None
        if started is None and not f.get("waiting"):
            raise onboarding_service.OnboardingError("Enter the first billable day.")
        onboarding_service.set_billing(db, actor, demand, started, str(f.get("reason") or ""))
    except ValueError as e:
        db.rollback()
        text = str(e) if isinstance(e, onboarding_service.OnboardingError) else "Enter a valid date."
        return RedirectResponse(f"/demands/{ref}?err={quote(text)}#billing", status_code=303)
    msg = f"Billing started on {started:%d %b %Y}" if started else "Reason saved: billing has not started"
    return RedirectResponse(f"/demands/{ref}?msg={quote(msg)}#billing", status_code=303)


@router.post("/demands/{ref}/billable")
async def mark_billable(
    ref: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    """A proactive, non-billable position: the owner records the day the client started billing."""
    demand = get_visible(db, actor, ref)
    if demand is None:
        raise HTTPException(404, "Demand not found, or not visible to you.")
    f = await request.form()
    raw = str(f.get("billable_from") or "")
    try:
        when = None if f.get("undo") else (date.fromisoformat(raw) if raw else None)
        if when is None and not f.get("undo"):
            raise ValueError("Enter the day the client's billing started.")
        costing_service.mark_billable(db, actor, demand, when)
    except ValueError as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}?err={quote(str(e))}#costing", status_code=303)
    msg = (
        f"Marked billable from {when:%d %b %Y}; costing stops"
        if when
        else "Back to non-billable; costing resumes"
    )
    return RedirectResponse(f"/demands/{ref}?msg={quote(msg)}#costing", status_code=303)


@router.post("/demands/{ref}/client-interview")
def start_client_interview(
    ref: str, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    """The demand owner marks that the client has started interviewing."""
    demand = get_visible(db, actor, ref)
    if demand is None:
        raise HTTPException(404, "Demand not found, or not visible to you.")
    try:
        pipeline_service.start_client_interview(db, actor, demand)
    except ValueError as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}?err={quote(str(e))}#client", status_code=303)
    return RedirectResponse(f"/demands/{ref}?msg=Client+interview+started#client", status_code=303)


@router.post("/demands/{ref}/client-decision")
async def record_client_decision(
    ref: str, request: Request, actor: Actor = Depends(current_user), db: Session = Depends(get_db)
) -> RedirectResponse:
    """The client interviewed a candidate: the demand owner records whether they were selected."""
    demand = get_visible(db, actor, ref)
    if demand is None:
        raise HTTPException(404, "Demand not found, or not visible to you.")
    f = await request.form()
    try:
        cand = pipeline_service.record_client_decision(
            db,
            actor,
            demand,
            int(str(f.get("candidate_id") or 0)),
            str(f.get("outcome") or ""),
            str(f.get("channel") or ""),
            str(f.get("note") or ""),
        )
    except ValueError as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}?err={quote(str(e))}#client", status_code=303)
    if cand.client_outcome == "select":
        msg = f"{cand.name} selected by the client; offer approval raised"
    else:
        msg = f"{cand.name} recorded as not selected by the client"
    return RedirectResponse(f"/demands/{ref}?msg={quote(msg)}#offers", status_code=303)


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
) -> Response:
    demand = get_visible(db, actor, ref)
    if demand is None or not (demand.jd_path or demand.jd_text):
        raise HTTPException(404, "No job description.")
    if demand.jd_text:  # typed on the demand form
        return PlainTextResponse(demand.jd_text)
    try:
        path = storage.open_path(demand.jd_path or "")
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


def _to_respond(db: Session, actor: Actor, demand: Demand) -> list[dict[str, Any]]:
    """Open escalations on this demand that this person has to answer, with what they can choose."""
    account = get_account(db, demand.account_id)
    out = []
    for e in db.scalars(
        select(Escalation)
        .where(Escalation.demand_id == demand.id, Escalation.status == "open")
        .order_by(Escalation.opened_at)
    ):
        if escalation_service.can_resolve(actor, e, demand):
            out.append(
                {
                    "e": e,
                    "actions": escalation_service.allowed_actions(db, account, e, demand),
                    "reasons": account.settings.reasons_for(e.type),
                    "steps": account.settings.rule_for(e.type).steps,
                    "waiting": escalation_service.given_more_time(e),
                    "waiting_note": (e.events[-1].note or "")
                    if escalation_service.given_more_time(e)
                    else "",
                }
            )
    return out


def _sheet_doj(db: Session, demand: Demand) -> bool:
    """Is the joining date shown the BCM sheet's (True), or the owner's expected date (False)?"""
    return (
        current_doj(db, demand.account_id).get(demand.id) != demand.expected_doj
        or demand.expected_doj is None
    )
