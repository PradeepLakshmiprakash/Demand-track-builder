"""Requests to the Administrator: raised by the lead admin, carried out by the Administrator."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import User, UserAccount
from app.models.admin_request import KINDS
from app.services import request_service
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
        examples=request_service.KINDS_FOR.get(actor.role, {}),
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


@router.post("/requests")
async def raise_request(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    if actor.role is Role.ADMINISTRATOR:
        raise HTTPException(403, "The Administrator carries requests out; they don't raise them.")
    f = await request.form()
    try:
        req = request_service.raise_request(db, actor, str(f.get("kind") or ""), str(f.get("details") or ""))
    except RequestError as e:
        db.rollback()
        return RedirectResponse(f"/requests?err={quote(str(e))}", status_code=303)
    msg = f"Request #{req.id} sent to the Administrator"
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
