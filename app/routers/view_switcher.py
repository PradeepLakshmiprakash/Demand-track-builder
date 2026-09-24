"""Dev-only "View as" persona switcher. Removed (config flag off) when SSO lands in Phase 7."""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.enums import Role
from app.core.nav import home_for
from app.core.security import ACCOUNT_COOKIE, VIEW_AS_COOKIE, usable_memberships
from app.models import User
from app.services import user_service
from app.services.user_service import Member

router = APIRouter(tags=["view as (dev only)"])


@dataclass
class SwitcherGroup:
    label: str
    users: list[Member]


def switcher_options(db: Session, account_id: int | None) -> list[SwitcherGroup]:
    """The people of the account being viewed, by their role in it."""
    if account_id is None:
        return []
    members = user_service.list_users(db, account_id)
    return [SwitcherGroup(role.label, [m for m in members if m.role == role.value]) for role in Role]


@router.get("/view-as/{user_id}")
def view_as(user_id: int, request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    if not get_settings().view_switcher_enabled:
        raise HTTPException(404)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such user")
    # Stay in the account being viewed when this person works there; else go to their first account.
    raw = request.cookies.get(ACCOUNT_COOKIE, "")
    usable = usable_memberships(user)
    m = next((x for x in usable if str(x.account_id) == raw), usable[0] if usable else None)
    resp = RedirectResponse(home_for(m.role_enum) if m else "/", status_code=303)
    resp.set_cookie(VIEW_AS_COOKIE, str(user.id), httponly=True, samesite="lax")
    if m is not None:
        resp.set_cookie(ACCOUNT_COOKIE, str(m.account_id), httponly=True, samesite="lax")
    return resp
