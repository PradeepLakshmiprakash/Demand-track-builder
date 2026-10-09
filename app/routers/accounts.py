"""Switching between the accounts a person works in, and the Administrator's Accounts screen."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import ACCOUNT_COOKIE, Actor, current_user, require_screen
from app.core.templating import render
from app.models import Account
from app.services import platform_service
from app.services.platform_service import PlatformError

router = APIRouter(tags=["accounts"])
guard = require_screen("accounts")  # the Administrator


@router.get("/switch-account/{account_id}")
def switch_account(account_id: int, actor: Actor = Depends(current_user)) -> RedirectResponse:
    """Work in another of your accounts. Your role there is your role in that account."""
    if account_id not in {a for a, _ in actor.accounts}:
        raise HTTPException(404, "You don't work in that account.")
    name = dict(actor.accounts)[account_id]
    resp = RedirectResponse(f"/?msg={quote('Now working in ' + name)}", status_code=303)
    resp.set_cookie(ACCOUNT_COOKIE, str(account_id), httponly=True, samesite="lax")
    return resp


@router.get("/platform/accounts", response_class=HTMLResponse)
def accounts_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    return render(
        request,
        "platform/accounts.html",
        actor,
        db,
        rows=platform_service.list_accounts(db),
        templates=list(db.scalars(select(Account).where(Account.active).order_by(Account.name))),
    )


@router.post("/platform/accounts")
async def create_account(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    f = await request.form()
    copy = str(f.get("copy_from") or "")
    try:
        account = platform_service.create_account(
            db,
            actor,
            name=str(f.get("name") or ""),
            timezone=str(f.get("timezone") or "").strip(),
            copy_from=int(copy) if copy.isdigit() else None,
            admin_name=str(f.get("admin_name") or ""),
            admin_email=str(f.get("admin_email") or ""),
        )
    except PlatformError as e:
        db.rollback()
        return RedirectResponse(f"/platform/accounts?err={quote(str(e))}", status_code=303)
    msg = f"{account.name} created. Its GTD admin team lead sets it up in Account settings."
    return RedirectResponse(f"/platform/accounts?msg={quote(msg)}", status_code=303)


@router.post("/platform/accounts/{account_id}/active")
async def set_active(
    account_id: int,
    request: Request,
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    active = (await request.form()).get("active") == "1"
    try:
        account = platform_service.set_account_active(db, actor, account_id, active)
    except PlatformError as e:
        db.rollback()
        return RedirectResponse(f"/platform/accounts?err={quote(str(e))}", status_code=303)
    msg = f"{account.name} {'reactivated' if active else 'deactivated'}"
    return RedirectResponse(f"/platform/accounts?msg={quote(msg)}", status_code=303)
