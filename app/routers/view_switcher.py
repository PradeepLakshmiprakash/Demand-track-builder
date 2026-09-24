"""Dev-only "View as" persona switcher. Removed (config flag off) when SSO lands in Phase 7."""

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.db import get_db
from app.core.enums import Role
from app.core.nav import home_for
from app.core.security import VIEW_AS_COOKIE
from app.models import User

router = APIRouter(tags=["view as (dev only)"])


@dataclass
class SwitcherGroup:
    label: str
    users: list[User]


def switcher_options(db: Session) -> list[SwitcherGroup]:
    users = list(
        db.scalars(
            select(User).options(selectinload(User.business_units)).order_by(User.active.desc(), User.name)
        )
    )
    return [SwitcherGroup(role.label, [u for u in users if u.role == role.value]) for role in Role]


@router.get("/view-as/{user_id}")
def view_as(user_id: int, db: Session = Depends(get_db)) -> RedirectResponse:
    if not get_settings().view_switcher_enabled:
        raise HTTPException(404)
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "No such user")
    resp = RedirectResponse(home_for(user.role_enum), status_code=303)
    resp.set_cookie(VIEW_AS_COOKIE, str(user.id), httponly=True, samesite="lax")
    return resp
