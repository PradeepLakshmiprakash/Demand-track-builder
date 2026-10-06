"""Rate calculator: "the client pays $100 an hour: which practice and grade can we put forward?"

The same idea as Acquisition Central's calculator. It reads the rate card and the margin threshold the
approvals use, so what it says is how an offer would really be routed. Nothing is saved.
"""

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.services import rate_card_service
from app.services.account_service import get_account
from app.services.demand_service import account_today

router = APIRouter(tags=["margin calculator"])
guard = require_screen("calculator")


@router.get("/margin-calculator", response_class=HTMLResponse)
def calculator(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    account = get_account(db, actor.account_id)
    cfg = account.settings
    q = request.query_params
    threshold = Decimal(account.margin_threshold)
    practice = q.get("practice", "") if q.get("practice", "") in cfg.practices else ""
    error = None
    bill: Decimal | None = None
    hold = threshold
    try:
        bill = Decimal(q.get("bill_rate") or "100")
        if bill <= 0 or bill > 10000:
            raise InvalidOperation
    except InvalidOperation:
        bill, error = None, "Enter what the client pays per hour as a number above 0."
    try:
        if q.get("hold"):
            hold = Decimal(q["hold"])
            if hold < 0 or hold >= 100:
                raise InvalidOperation
    except InvalidOperation:
        hold, error = threshold, "The margin to hold must be between 0 and 99."

    today = account_today(db, account.id)
    rank = cfg.grade_rank
    rows = rate_card_service.offerings(db, account.id, bill, hold, today) if bill else []

    def best_in(p: str) -> rate_card_service.Offering | None:
        """The most senior grade this practice can afford at the margin held."""
        fits = sorted((r for r in rows if r.practice == p and r.fits), key=lambda r: rank(r.grade))
        return fits[-1] if fits else None

    best = {p: best_in(p) for p in cfg.practices}
    if practice:  # the grades around what the rate affords: the best one, with two either side
        mine = sorted((r for r in rows if r.practice == practice), key=lambda r: rank(r.grade))
        top = best[practice]
        at = mine.index(top) if top is not None and top in mine else 0
        options = mine[max(0, at - 2) : at + 3]
    else:  # the best each practice can do, dearest first
        options = sorted((b for b in best.values() if b), key=lambda r: (-r.cost, r.practice))
    return render(
        request,
        "margin_calculator/index.html",
        actor,
        db,
        cfg=cfg,
        account=account,
        v={"practice": practice, "bill_rate": q.get("bill_rate", "100"), "hold": f"{hold:g}"},
        bill=bill,
        hold=hold,
        threshold=threshold,
        ceiling=rate_card_service.ceiling(bill, hold) if bill else None,
        options=options,
        pick=best.get(practice) if practice else (options[0] if options else None),
        has_card=bool(rows),
        today=today,
        error=error,
    )
