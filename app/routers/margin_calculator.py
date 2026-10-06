"""Margin calculator, in three pages.

Individual contribution margin: "the client pays $100 an hour for a C2 in CCA-FS: what margin do we make?"
Team contribution margin: several roles, each with its own client rate; the blended margin of the team.
Pod contribution margin: one price a month for a whole pod, whose members can be part-time on it.

The individual page:

Client rate, practice and grade in; the margin for that grade out, with a short list of alternatives: in
the same practice, and in the practices that take the same technical skills. It reads the rate card
and the margin threshold the approvals use, so what it says is how an offer would really be routed.
Nothing is saved.
"""

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.account_config import AccountConfig
from app.core.db import get_db
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.services import rate_card_service
from app.services.account_service import get_account
from app.services.demand_service import account_today
from app.services.rate_card_service import Member

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
        tab="individual",
    )


ROWS_MAX = 40  # lines read from one form


def _num(raw: str, lo: Decimal, hi: Decimal) -> Decimal | None:
    try:
        v = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return v if lo <= v <= hi else None


def _members(request: Request, cfg: AccountConfig, *, with_rate: bool) -> tuple[list[Member], list[str]]:
    """The lines typed into a team or pod form. A line with no practice and no grade is skipped."""
    q = request.query_params
    cols = {k: q.getlist(k) for k in ("practice", "grade", "count", "rate", "allocation")}
    n = max((len(v) for v in cols.values()), default=0)
    out: list[Member] = []
    problems: list[str] = []
    for i in range(min(n, ROWS_MAX)):

        def cell(key: str, i: int = i) -> str:
            return cols[key][i].strip() if i < len(cols[key]) else ""

        practice, grade = cell("practice"), cell("grade")
        if not practice and not grade:
            continue
        where = f"Line {i + 1}"
        if practice not in cfg.practices or grade not in cfg.grades:
            problems.append(f"{where}: choose both the practice and the grade.")
            continue
        count = _num(cell("count") or "1", Decimal(1), Decimal(200))
        if count is None or count != count.to_integral_value():
            problems.append(f"{where}: the number of people must be a whole number from 1 to 200.")
            continue
        m = Member(practice, grade, int(count))
        if with_rate:
            m.rate = _num(cell("rate"), Decimal("0.01"), Decimal(10000))
            if m.rate is None:
                problems.append(f"{where}: enter the client rate per hour for {grade} in {practice}.")
                continue
        else:
            alloc = _num(cell("allocation") or "100", Decimal(1), Decimal(100))
            if alloc is None:
                problems.append(f"{where}: allocation must be between 1 and 100 per cent.")
                continue
            m.allocation = alloc
        out.append(m)
    return out, problems


def _blank_rows(members: list[Member]) -> int:
    """Empty lines to start with; after that the Add button adds them."""
    return 0 if members else 3


@router.get("/margin-calculator/team", response_class=HTMLResponse)
def team_calculator(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    account = get_account(db, actor.account_id)
    cfg = account.settings
    today = account_today(db, account.id)
    members, problems = _members(request, cfg, with_rate=True)
    result = rate_card_service.team_contribution(db, account, members, today) if members else None
    return render(
        request,
        "margin_calculator/team.html",
        actor,
        db,
        cfg=cfg,
        account=account,
        threshold=Decimal(account.margin_threshold),
        members=members,
        blanks=_blank_rows(members),
        problems=problems,
        r=result,
        today=today,
        tab="team",
    )


@router.get("/margin-calculator/pod", response_class=HTMLResponse)
def pod_calculator(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    account = get_account(db, actor.account_id)
    cfg = account.settings
    today = account_today(db, account.id)
    members, problems = _members(request, cfg, with_rate=False)
    raw_price = request.query_params.get("price", "").strip()
    price = _num(raw_price, Decimal(1), Decimal(100_000_000)) if raw_price else None
    if raw_price and price is None:
        problems.append("Enter the pod's price per month as a number above 0.")
    result = (
        rate_card_service.pod_contribution(db, account, members, price, today) if members and price else None
    )
    return render(
        request,
        "margin_calculator/pod.html",
        actor,
        db,
        cfg=cfg,
        account=account,
        threshold=Decimal(account.margin_threshold),
        members=members,
        blanks=_blank_rows(members),
        problems=problems,
        price=raw_price,
        r=result,
        today=today,
        tab="pod",
    )
