"""Admin demand owner and admin team: candidates and the requisitions they're on, CVs from staffing's
emails, L2 requests, and the interview placeholders (staffing schedules; who interviews L2 is open)."""

from datetime import datetime
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core import storage
from app.core.db import get_db
from app.core.enums import InterviewStatus, Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import Account, Candidate, Demand, Interview, User
from app.services import interview_service as svc
from app.services.interview_service import InterviewError

router = APIRouter(tags=["candidates"])
guard = require_screen("candidates")

TABS = {
    "unmapped": "No requisition yet",
    "requests": "Extra rounds",
    "to_schedule": "To schedule or assign",
    "all": "All candidates",
}


def _int(v: object) -> int | None:
    s = str(v or "")
    return int(s) if s.isdigit() else None


def _back(
    tab: str, candidate_id: int | None = None, *, msg: str | None = None, err: str | None = None
) -> RedirectResponse:
    q = f"err={quote(err)}" if err else f"msg={quote(msg or 'Saved')}"
    anchor = f"#c{candidate_id}" if candidate_id else ""
    return RedirectResponse(f"/candidates?tab={tab}&{q}{anchor}", status_code=303)


@router.get("/candidates", response_class=HTMLResponse)
def candidates_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    tab = request.query_params.get("tab", "unmapped")
    tab = tab if tab in TABS else "unmapped"
    q = request.query_params.get("q", "").strip()
    rows: list[tuple[Candidate, Demand | None]] = [
        (c, d)
        for c, d in db.execute(
            select(Candidate, Demand)
            .outerjoin(Demand, Demand.id == Candidate.demand_id)
            .where(Candidate.account_id == actor.account_id)
            .order_by(Candidate.name)
        ).all()
    ]
    interviews: dict[int, list[Interview]] = {}
    for iv in db.scalars(
        select(Interview)
        .join(Candidate)
        .where(Candidate.account_id == actor.account_id)
        .order_by(Interview.id)
    ):
        interviews.setdefault(iv.candidate_id, []).append(iv)

    def has(cid: int, *statuses: InterviewStatus) -> bool:
        return any(iv.status in {s.value for s in statuses} for iv in interviews.get(cid, []))

    counts = {
        "unmapped": sum(d is None for _, d in rows),
        "requests": sum(has(c.id, InterviewStatus.REQUESTED) for c, _ in rows),
        "to_schedule": sum(has(c.id, InterviewStatus.OPEN) for c, _ in rows),
        "all": len(rows),
    }
    if q:
        wanted = {m.candidate.id for m in svc.search(db, actor.account_id, q, limit=50)}
        rows = [(c, d) for c, d in rows if c.id in wanted]
    if tab == "unmapped":
        rows = [(c, d) for c, d in rows if d is None]
    elif tab == "requests":
        rows = [(c, d) for c, d in rows if has(c.id, InterviewStatus.REQUESTED)]
    elif tab == "to_schedule":
        rows = [(c, d) for c, d in rows if has(c.id, InterviewStatus.OPEN)]
    ivs = [iv for ivl in interviews.values() for iv in ivl]
    people = {iv.interviewer_id for iv in ivs if iv.interviewer_id} | {
        iv.requested_by for iv in ivs if iv.requested_by
    }
    ctx: dict[str, Any] = {
        "tab": tab,
        "tabs": TABS,
        "counts": counts,
        "rows": rows,
        "q": q,
        "interviews": interviews,
        "names": {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(people)))}
        if people
        else {},
        "requisitions": svc.open_requisitions(db, actor.account_id),
        "interviewers": list(
            db.scalars(
                select(User)
                .where(
                    User.active, User.role == Role.INTERVIEWER.value, User.accounts.any(id=actor.account_id)
                )
                .order_by(User.name)
            )
        ),
        "is_admin": actor.role is Role.ADMIN,
    }
    return render(request, "candidates/index.html", actor, db, **ctx)


@router.post("/candidates/add")
async def add(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    try:
        c = svc.add_candidate(db, actor, str(form.get("name") or ""), _int(form.get("demand_id")))
    except InterviewError as e:
        db.rollback()
        return _back("all", err=str(e))
    return _back("all" if c.demand_id else "unmapped", c.id, msg=f"{c.name} added")


@router.post("/candidates/{candidate_id}/map")
async def map_(
    candidate_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    demand_id = _int((await request.form()).get("demand_id"))
    try:
        if demand_id is None:
            raise InterviewError("Choose the requisition.")
        c = svc.map_candidate(db, actor, candidate_id, demand_id)
    except InterviewError as e:
        db.rollback()
        return _back("unmapped", candidate_id, err=str(e))
    d = db.get_one(Demand, c.demand_id)
    return _back("unmapped", msg=f"{c.name} is on {d.gtd_req_id or d.app_ref}")


@router.post("/candidates/{candidate_id}/cv")
async def upload_cv(
    candidate_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    file = (await request.form()).get("cv")
    if not isinstance(file, UploadFile) or not file.filename:
        return _back("all", candidate_id, err="Choose the CV file.")
    try:
        c = svc.attach_cv(db, actor, candidate_id, file.filename, await file.read())
    except InterviewError as e:
        db.rollback()
        return _back("all", candidate_id, err=str(e))
    return _back("all", c.id, msg=f"CV attached for {c.name}")


@router.get("/candidates/{candidate_id}/cv")
def download_cv(
    candidate_id: int, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> FileResponse:
    c = db.get(Candidate, candidate_id)
    if c is None or c.account_id != actor.account_id or not c.cv_path:
        raise HTTPException(404, "No CV.")
    try:
        path = storage.open_path(c.cv_path)
    except FileNotFoundError as e:
        raise HTTPException(404, "The CV file is missing.") from e
    return FileResponse(path, filename=path.name)


@router.post("/candidates/{candidate_id}/schedule")
async def schedule(
    candidate_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    raw = str(form.get("when") or "")
    try:
        tz = ZoneInfo(db.get_one(Account, actor.account_id).settings.timezone)
        when = datetime.fromisoformat(raw).replace(tzinfo=tz) if raw else None  # typed in account time
        iv = svc.schedule(
            db,
            actor,
            candidate_id=candidate_id,
            round_=str(form.get("round") or ""),
            interviewer_id=_int(form.get("interviewer_id")),
            when=when,
            interview_id=_int(form.get("interview_id")),
        )
    except (InterviewError, ValueError) as e:
        db.rollback()
        return _back(str(form.get("tab") or "to_schedule"), candidate_id, err=str(e))
    msg = "Invite sent to the interviewer" if iv.status == "scheduled" else "Saved as to be scheduled"
    return _back(str(form.get("tab") or "to_schedule"), candidate_id, msg=msg)
