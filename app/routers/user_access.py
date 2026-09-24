"""Admin: add, edit, deactivate users and set what they see."""

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import ALLOWED_SCOPES, Role, Scope
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import BusinessUnit, User
from app.schemas.user_access import UserForm, UserOut
from app.services import user_service
from app.services.account_service import get_account
from app.services.user_service import UserAccessError

router = APIRouter(tags=["user access"])
guard = require_screen("users")


def _form_from(data: Any) -> dict[str, Any]:
    return {
        "name": data.get("name", ""),
        "email": data.get("email", ""),
        "role": data.get("role", ""),
        "level": data.get("level"),
        "scope": data.get("scope"),
        "bu_ids": data.getlist("bu_ids"),
        "practices": data.getlist("practices"),
        "skills": data.get("skills", ""),
        "max_grade": data.get("max_grade"),
    }


def _page(
    request: Request,
    actor: Actor,
    db: Session,
    selected: User | None,
    *,
    draft: dict[str, Any] | None = None,
    error: str | None = None,
) -> HTMLResponse:
    account = get_account(db, actor.account_id)
    bus = list(
        db.scalars(
            select(BusinessUnit).where(BusinessUnit.account_id == actor.account_id).order_by(BusinessUnit.id)
        )
    )
    return render(
        request,
        "user_access/index.html",
        actor,
        db,
        status_code=400 if error else 200,
        users=user_service.list_users(db, actor.account_id),
        selected=selected,
        draft=draft,
        error=error,
        roles=list(Role),
        allowed_scopes={r.value: [s.value for s in ALLOWED_SCOPES[r]] for r in Role},
        all_scopes=list(Scope),
        bus=bus,
        account=account,
        cfg=account.settings,
    )


@router.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request,
    id: int | None = None,
    new: bool = False,
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    users = user_service.list_users(db, actor.account_id)
    selected = None
    if not new:
        selected = next((u for u in users if u.id == id), None) if id else (users[0] if users else None)
    return _page(request, actor, db, selected)


@router.post("/users", response_class=HTMLResponse, response_model=None)
async def create_user(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    raw = _form_from(await request.form())
    try:
        user = user_service.create_user(db, actor, UserForm.model_validate(raw))
    except (ValidationError, UserAccessError) as e:
        db.rollback()
        return _page(request, actor, db, None, draft=raw, error=_message(e))
    return RedirectResponse(f"/users?id={user.id}&msg={quote(f'{user.name} added')}", status_code=303)


@router.post("/users/{user_id}", response_class=HTMLResponse, response_model=None)
async def update_user(
    user_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    raw = _form_from(await request.form())
    try:
        user = user_service.update_user(db, actor, user_id, UserForm.model_validate(raw))
    except (ValidationError, UserAccessError) as e:
        db.rollback()
        selected = db.get(User, user_id)
        return _page(request, actor, db, selected, draft=raw, error=_message(e))
    return RedirectResponse(f"/users?id={user.id}&msg=Changes+saved", status_code=303)


@router.post("/users/{user_id}/active")
async def set_active(
    user_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    active = (await request.form()).get("active") == "1"
    try:
        user = user_service.set_active(db, actor, user_id, active)
    except UserAccessError as e:
        db.rollback()
        return RedirectResponse(f"/users?id={user_id}&err={quote(str(e))}", status_code=303)
    msg = f"{user.name} {'reactivated' if active else 'deactivated'}"
    return RedirectResponse(f"/users?id={user_id}&msg={quote(msg)}", status_code=303)


@router.get("/api/users")
def users_json(actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> list[UserOut]:
    return [
        UserOut(
            id=u.id,
            name=u.name,
            email=u.email,
            role=u.role_enum,
            level=u.level,
            scope=u.scope_enum,
            active=u.active,
            business_units=[b.name for b in u.business_units],
            practices=u.practices,
        )
        for u in user_service.list_users(db, actor.account_id)
    ]


def _message(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in e.errors())
    return str(e)
