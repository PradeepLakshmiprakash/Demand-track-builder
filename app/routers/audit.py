"""Change history: who changed what, from what to what, and when. Read-only."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import AuditEntry

router = APIRouter(tags=["change history"])
guard = require_screen("audit")
PAGE = 50
ENTITIES = ("demand", "access", "user", "account", "rate", "business unit", "interviewer profile")
# Money fields: shown only to roles that see rates elsewhere in the app.
RATES = {"client_rate", "cost_rate"}


@router.get("/audit", response_class=HTMLResponse)
def audit_page(
    request: Request,
    entity: str = "",
    q: str = "",
    page: int = 1,
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    stmt = select(AuditEntry).where(AuditEntry.account_id == actor.account_id)
    if entity in ENTITIES:
        stmt = stmt.where(AuditEntry.entity == entity)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(AuditEntry.entity_label.ilike(like), AuditEntry.actor_name.ilike(like)))
    page = max(page, 1)
    rows = list(
        db.scalars(
            stmt.order_by(AuditEntry.at.desc(), AuditEntry.id.desc())
            .offset((page - 1) * PAGE)
            .limit(PAGE + 1)
        )
    )
    return render(
        request,
        "audit/index.html",
        actor,
        db,
        rows=rows[:PAGE],
        more=len(rows) > PAGE,
        page=page,
        entity=entity,
        q=q,
        entities=ENTITIES,
        hide=set() if actor.can_see_bill_rate or actor.role.value == "administrator" else RATES,
    )
