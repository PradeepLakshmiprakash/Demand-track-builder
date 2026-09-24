"""Admin demand owner: the vendor rate card (dated cost rates per hour)."""

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import Account
from app.services import margin_service, rate_card_service
from app.services.demand_service import account_today
from app.services.rate_card_service import RateCardError

router = APIRouter(tags=["rate card"])
guard = require_screen("rate_card")


@router.get("/rate-card", response_class=HTMLResponse)
def rate_card_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    account = db.get_one(Account, actor.account_id)
    cfg = account.settings
    today = account_today(db, actor.account_id)
    history = request.query_params.get("history") == "1"
    rows = rate_card_service.listing(db, actor.account_id, on=today, include_history=history)
    rows.sort(
        key=lambda r: (r.channel, r.region, cfg.grade_rank(r.grade), r.practice or "", r.effective_from)
    )
    return render(
        request,
        "rate_card/index.html",
        actor,
        db,
        rows=rows,
        cfg=cfg,
        channels={c.key: c.label for c in cfg.supply_channels},
        today=today,
        history=history,
    )


def _date(v: object) -> date | None:
    s = str(v or "")
    return date.fromisoformat(s) if s else None


@router.post("/rate-card")
async def add(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    f = await request.form()
    try:
        start = _date(f.get("effective_from"))
        if start is None:
            raise RateCardError("Enter the date the rate starts.")
        row = rate_card_service.add_rate(
            db,
            actor.account_id,
            actor.id,
            grade=str(f.get("grade") or ""),
            practice=str(f.get("practice") or ""),
            region=str(f.get("region") or ""),
            channel=str(f.get("channel") or ""),
            cost_rate=str(f.get("cost_rate") or ""),
            effective_from=start,
            effective_to=_date(f.get("effective_to")),
        )
    except (RateCardError, ValueError) as e:
        db.rollback()
        return RedirectResponse(f"/rate-card?err={quote(str(e))}", status_code=303)
    changed = margin_service.reprice_pending(db, actor.account_id)
    msg = f"Rate added: {row.grade} {row.channel} {row.region} {row.cost_rate}/h"
    msg += f" from {row.effective_from:%d %b %Y}"
    if changed:
        msg += f". {changed} waiting offer(s) re-priced"
    return RedirectResponse(f"/rate-card?msg={quote(msg)}", status_code=303)


@router.post("/rate-card/{rate_id}/end")
async def end(
    rate_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    try:
        last = _date((await request.form()).get("last_day"))
        if last is None:
            raise RateCardError("Enter the rate's last day.")
        rate_card_service.end_rate(db, actor.account_id, rate_id, last)
    except (RateCardError, ValueError) as e:
        db.rollback()
        return RedirectResponse(f"/rate-card?err={quote(str(e))}", status_code=303)
    margin_service.reprice_pending(db, actor.account_id)
    return RedirectResponse("/rate-card?msg=Rate+ended", status_code=303)
