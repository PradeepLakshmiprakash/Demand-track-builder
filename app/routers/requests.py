"""Requests to the Administrator: raised by the GTD admin team lead, carried out by the Administrator."""

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import EscalationType, MainStage, Responsible, Role, Severity
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import BusinessUnit, Demand, User, UserAccount
from app.models.admin_request import KINDS
from app.services import rate_card_service, request_service
from app.services.account_service import get_account
from app.services.demand_service import account_today, visible_demands
from app.services.request_service import RequestError

router = APIRouter(tags=["requests"])
guard = require_screen("requests")


@router.get("/requests", response_class=HTMLResponse)
def requests_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    everyone = request_service.sees_all(actor)
    rows = request_service.list_requests(db, actor.account_id, None if everyone else actor.id)
    ids = {r.requested_by for r in rows} | {r.handled_by for r in rows if r.handled_by}
    people = {i: n for i, n in db.execute(select(User.id, User.name).where(User.id.in_(ids)))} if ids else {}
    return render(
        request,
        "requests/index.html",
        actor,
        db,
        rows=rows,
        open_count=sum(r.status == "open" for r in rows),
        people=people,
        kinds=KINDS,
        kinds_offered=request_service.kinds_for(actor.role),
        picked_kind=request.query_params.get("kind", ""),
        **_form_context(db, actor),
        can_raise=actor.role is not Role.ADMINISTRATOR,
        can_handle=actor.role is Role.ADMINISTRATOR,
        sees_all=everyone,
        special=request_service.special_access(db, actor.account_id, actor.id),
        roles={
            u.id: m.role_enum.label
            for u, m in db.execute(
                select(User, UserAccount)
                .join(UserAccount, UserAccount.user_id == User.id)
                .where(UserAccount.account_id == actor.account_id, User.id.in_(ids))
            )
        }
        if ids
        else {},
    )


def _form_context(db: Session, actor: Actor) -> dict[str, Any]:
    """What the request form offers, and what the person already has."""
    if actor.role is Role.ADMINISTRATOR:
        return {}
    account = get_account(db, actor.account_id)
    cfg = account.settings
    held = {r.details for r in request_service.special_access(db, actor.account_id, actor.id)}
    people = request_service.interviewers(db, actor.account_id)
    mine = next(((u, p) for u, p in people if u.id == actor.id), None)
    today = account_today(db, actor.account_id)
    return {
        "cfg": cfg,
        "today": today,
        "all_bus": list(
            db.scalars(
                select(BusinessUnit.name)
                .where(BusinessUnit.account_id == actor.account_id)
                .order_by(BusinessUnit.name)
            )
        ),
        "role_options": request_service.ASSIGNABLE_ROLES,
        "special_options": [(s, s in held) for s in request_service.SPECIAL.get(actor.role, ())],
        "settings_options": request_service.SETTINGS,
        "stages": list(MainStage),
        "triggers": [(t, cfg.rule_for(t.value)) for t in EscalationType],
        "severities": list(Severity),
        "responsibles": list(Responsible),
        "interviewers": people,
        "my_profile": mine[1] if mine else None,
        "skill_options": request_service.skill_options(db, actor.account_id),
        "demands": [
            d
            for d in db.scalars(visible_demands(actor).order_by(Demand.app_ref.desc()))
            if d.status != "draft"
        ][:200],
        "data_fields": request_service.DATA_FIELDS,
        "card": rate_card_service.grid(db, actor.account_id, today),
    }


@router.post("/requests")
async def raise_request(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    if actor.role is Role.ADMINISTRATOR:
        raise HTTPException(403, "The Administrator carries requests out; they don't raise them.")
    f = await request.form()
    kind = str(f.get("kind") or "")
    try:
        made = [
            request_service.raise_request(db, actor, kind, details)
            for details in request_service.compose(db, actor, kind, f)
        ]
    except RequestError as e:
        db.rollback()
        return RedirectResponse(f"/requests?err={quote(str(e))}&kind={quote(kind)}#new", status_code=303)
    numbers = ", ".join(f"#{r.id}" for r in made)
    msg = f"Request{'s' if len(made) > 1 else ''} {numbers} sent to the Administrator"
    return RedirectResponse(f"/requests?msg={quote(msg)}", status_code=303)


@router.post("/requests/{request_id}")
async def handle(
    request_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    if actor.role is not Role.ADMINISTRATOR:
        raise HTTPException(403, "Only the Administrator marks requests done.")
    f = await request.form()
    try:
        req = request_service.handle(
            db, actor, request_id, str(f.get("status") or ""), str(f.get("note") or "")
        )
    except RequestError as e:
        db.rollback()
        return RedirectResponse(f"/requests?err={quote(str(e))}", status_code=303)
    return RedirectResponse(
        f"/requests?msg={quote(f'Request #{req.id} marked {req.status}')}", status_code=303
    )
