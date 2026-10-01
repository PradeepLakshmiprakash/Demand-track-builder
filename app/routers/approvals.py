"""Offer margin approvals: the demand owner decides at or above the cut-off (the GTD team admin is
notified), leadership below it."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import Account
from app.services import margin_service
from app.services.margin_service import ApprovalError

router = APIRouter(tags=["offer approvals"])
guard = require_screen("approvals")


@router.get("/approvals", response_class=HTMLResponse)
def approvals_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    account = db.get_one(Account, actor.account_id)
    cfg = account.settings
    return render(
        request,
        "approvals/index.html",
        actor,
        db,
        b=margin_service.board(db, actor),
        cutoff=account.margin_threshold,
        channels={c.key: c.label for c in cfg.supply_channels},
        is_admin=actor.role is Role.ADMIN,
    )


def _back(msg: str | None = None, err: str | None = None, anchor: str = "") -> RedirectResponse:
    q = f"err={quote(err)}" if err else f"msg={quote(msg or 'Saved')}"
    return RedirectResponse(f"/approvals?{q}{anchor}", status_code=303)


@router.post("/approvals/{approval_id}/decide")
async def decide(
    approval_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    f = await request.form()
    try:
        a = margin_service.decide(
            db, actor, approval_id, str(f.get("decision") or ""), str(f.get("comment") or "")
        )
    except ApprovalError as e:
        db.rollback()
        return _back(err=str(e), anchor=f"#a{approval_id}")
    return _back(f"Offer {a.decision} at {a.margin_pct}% margin")


@router.post("/approvals/{approval_id}/channel")
async def set_channel(
    approval_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    if actor.role is not Role.ADMIN:
        return _back(err="Only the GTD team admin changes an offer's details.")
    try:
        margin_service.set_channel(db, actor, approval_id, str((await request.form()).get("channel") or ""))
    except ApprovalError as e:
        db.rollback()
        return _back(err=str(e))
    return _back("Supply channel set and offer re-priced")


@router.post("/approvals/request")
async def request_approval(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    if actor.role is not Role.ADMIN:
        return _back(err="Only the GTD team admin raises an offer approval.")
    f = await request.form()
    try:
        margin_service.request(
            db,
            actor,
            int(str(f.get("demand_id") or 0)),
            str(f.get("candidate") or ""),
            str(f.get("channel") or ""),
        )
    except (ApprovalError, ValueError) as e:
        db.rollback()
        return _back(err=str(e))
    return _back("Offer approval raised")
