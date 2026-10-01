"""Margin calculator: a what-if for the GTD team admin and leadership.

Pick the practice, grade and region and enter a bill rate: for every supply channel it shows today's
vendor cost from the rate card, the margin, who would approve an offer at that margin, and the
lowest bill rate that still meets the account's cut-off. It changes nothing.
"""

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.services import margin_service
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
    grade = q.get("grade", "")
    region = q.get("region") or (cfg.regions[0] if cfg.regions else "")
    practice = q.get("practice", "")
    error = None
    bill: Decimal | None = None
    if q.get("bill_rate"):
        try:
            bill = Decimal(q["bill_rate"])
            if bill <= 0 or bill > 10000:
                raise InvalidOperation
        except InvalidOperation:
            bill, error = None, "Enter the hourly bill rate as a number above 0."
    today = account_today(db, account.id)
    rows = (
        margin_service.what_if(
            db, account, grade=grade, practice=practice, region=region, bill=bill, on=today
        )
        if grade and not error
        else []
    )
    return render(
        request,
        "margin_calculator/index.html",
        actor,
        db,
        cfg=cfg,
        account=account,
        v={"grade": grade, "region": region, "practice": practice, "bill_rate": q.get("bill_rate", "")},
        rows=rows,
        bill=bill,
        today=today,
        error=error,
    )
