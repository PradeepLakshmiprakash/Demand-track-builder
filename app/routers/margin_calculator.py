"""Margin calculator: "the client pays $100 an hour for a C2 in CCA-FS: what margin do we make?"

Client rate, practice and grade in; the margin for that grade out, with a short list of alternatives: in
the same practice, and in the practices that take the same technical skills. It reads the rate card
and the margin threshold the approvals use, so what it says is how an offer would really be routed.
Nothing is saved.
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
    grade = q.get("grade", "") if q.get("grade", "") in cfg.grades else ""
    error = None
    bill: Decimal | None = None
    try:
        bill = Decimal(q.get("bill_rate") or "100")
        if bill <= 0 or bill > 10000:
            raise InvalidOperation
    except InvalidOperation:
        bill, error = None, "Enter what the client pays per hour as a number above 0."

    today = account_today(db, account.id)
    rows = rate_card_service.offerings(db, account.id, bill, threshold, today) if bill else []
    pick = next((r for r in rows if r.practice == practice and r.grade == grade), None)
    options = (
        rate_card_service.suggestions(rows, practice, grade, threshold, cfg.shared_stacks)
        if practice and grade
        else []
    )
    return render(
        request,
        "margin_calculator/index.html",
        actor,
        db,
        cfg=cfg,
        account=account,
        v={"practice": practice, "grade": grade, "bill_rate": q.get("bill_rate", "100")},
        bill=bill,
        threshold=threshold,
        ceiling=rate_card_service.ceiling(bill, threshold) if bill else None,
        options=options,
        pick=pick,
        has_card=bool(rows),
        today=today,
        error=error,
    )
