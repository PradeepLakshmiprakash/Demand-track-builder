"""Admin demand owner, admin team and leadership: open escalations, and resolving them.

Opened automatically; closed only with a reason and an action. L1 is resolved by the admin demand
owner or admin team, L2 by leadership.
"""

from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import EscalationType, ResolutionAction, Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import Account, User
from app.services import escalation_service as svc
from app.services.escalation_service import EscalationError

router = APIRouter(tags=["escalations"])
guard = require_screen("escalations")

CHIPS = {
    EscalationType.NOT_SUBMITTED: "esc",
    EscalationType.MISSING: "esc",
    EscalationType.DROPPED: "esc",
    EscalationType.INCORRECT: "esc",
    EscalationType.PAST_START: "risk",
    EscalationType.AGING: "blue",
    EscalationType.REJECTION_LIMIT: "gray",
    EscalationType.PANEL_SLA: "gray",
}


def _int(v: str | None) -> int | None:
    return int(v) if v and v.isdigit() else None


@router.get("/escalations", response_class=HTMLResponse)
def escalations_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    q = request.query_params
    status = q.get("status", "open")
    level = _int(q.get("level"))
    type_ = q.get("type") if q.get("type") in {t.value for t in EscalationType} else None
    items = svc.listing(db, actor.account_id, status=status, level=level, type_=type_)
    account = db.get_one(Account, actor.account_id)
    now = datetime.now(UTC)

    sel_id = _int(q.get("id"))
    selected = items[0] if items else None
    if sel_id:  # may be outside the current filter, e.g. just resolved
        pool = (
            items
            if any(e.id == sel_id for e, _ in items)
            else svc.listing(db, actor.account_id, status="all")
        )
        selected = next(((e, d) for e, d in pool if e.id == sel_id), None)

    ctx: dict[str, Any] = {
        "items": items,
        "status": status,
        "level": level,
        "type": type_,
        "types": list(EscalationType),
        "chips": {t.value: c for t, c in CHIPS.items()},
        "now": now,
        "selected": None,
    }
    if selected is not None:
        esc, d = selected
        people = {ev.actor_id for ev in esc.events if ev.actor_id} | (
            {esc.resolved_by} if esc.resolved_by else set()
        )
        names = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(people)))} if people else {}
        ctx |= {
            "selected": esc,
            "sd": d,
            "audience": svc.audience(db, account, esc, d),
            "actions": svc.allowed_actions(db, account, esc, d) if esc.status == "open" else [],
            "cleared": esc.status == "open" and svc.is_cleared(db, account, esc, d, now),
            "can_resolve": svc.can_resolve(actor, esc),
            "reasons": account.settings.resolution_reasons,
            "names": names,
            "action_labels": {a.value: a.label for a in ResolutionAction},
        }
    return render(request, "escalations/index.html", actor, db, **ctx)


def _back(esc_id: int, status: str, *, err: str | None = None, msg: str | None = None) -> RedirectResponse:
    key, text = ("err", err) if err else ("msg", msg or "Saved")
    return RedirectResponse(
        f"/escalations?status={status}&id={esc_id}&{key}={quote(text or '')}", status_code=303
    )


@router.post("/escalations/{esc_id}/resolve")
async def resolve(
    esc_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    raw_date = str(form.get("extend_to") or "")
    try:
        extend_to = date.fromisoformat(raw_date) if raw_date else None
        esc = svc.resolve(
            db,
            actor,
            esc_id,
            reason=str(form.get("reason") or ""),
            action=str(form.get("action") or ""),
            comment=str(form.get("comment") or ""),
            extend_to=extend_to,
        )
    except (EscalationError, ValueError) as e:
        db.rollback()
        return _back(esc_id, "open", err=str(e))
    if esc.status == "open":
        return _back(esc_id, "open", msg=f"Due date extended to {esc.due_at:%d %b}; back at L1")
    return _back(esc_id, "resolved", msg="Escalation resolved")


@router.post("/escalations/sweep")
def sweep_now(actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> RedirectResponse:
    """Run the two-hourly sweep now (admin roles), e.g. right after changing thresholds."""
    if actor.role not in (Role.ADMIN, Role.ADMIN_TEAM):
        return RedirectResponse("/escalations?err=Only+the+admin+roles+can+run+the+sweep", status_code=303)
    result = svc.sweep(db, actor.account_id)
    return RedirectResponse(f"/escalations?msg={quote('Sweep: ' + result.message)}", status_code=303)
