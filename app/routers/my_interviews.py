"""Interviewer: my interviews, requisitions in my skills, and recording a recommendation for any
candidate found by name (staffing schedules outside the app, so this is the main way in)."""

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import Account, Candidate, Demand, Interview
from app.schemas.interviews import feedback_from_form
from app.services import interview_service as svc
from app.services.interview_service import InterviewError

router = APIRouter(tags=["my interviews"])
guard = require_screen("interviews")


def _int(v: object) -> int | None:
    s = str(v or "")
    return int(s) if s.isdigit() else None


@router.get("/interviews", response_class=HTMLResponse)
def my_interviews(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    account = db.get_one(Account, actor.account_id)
    mine = svc.assigned_to(db, actor)
    open_ = [x for x in mine if x[0].status == "scheduled"]
    sel_id = _int(request.query_params.get("id"))
    selected = next((x for x in open_ if x[0].id == sel_id), open_[0] if open_ else None)
    return render(
        request,
        "my_interviews/index.html",
        actor,
        db,
        open_=open_,
        done=[x for x in mine if x[0].status == "completed"],
        selected=selected,
        dims=account.settings.interview_ratings,
        new_reqs=svc.new_in_my_skills(db, actor),
        q=request.query_params.get("q", ""),
        results=svc.search(db, actor.account_id, request.query_params.get("q", "")),
        requisitions=svc.open_requisitions(db, actor.account_id),
    )


@router.post("/interviews/{interview_id}/feedback")
async def feedback_on_assigned(
    interview_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    iv = db.get(Interview, interview_id)
    cand = db.get(Candidate, iv.candidate_id) if iv is not None else None
    # Someone who interviews in two accounts gives feedback in the account the interview belongs to.
    if iv is None or cand is None or iv.interviewer_id != actor.id or cand.account_id != actor.account_id:
        raise HTTPException(404, "Interview not found.")
    account = db.get_one(Account, actor.account_id)
    form = await request.form()
    try:
        svc.record_feedback(
            db,
            account,
            actor.id,
            cand,
            feedback_from_form(form, account.settings.interview_ratings, iv.round),
            iv,
        )
    except InterviewError as e:
        db.rollback()
        return RedirectResponse(f"/interviews?id={interview_id}&err={quote(str(e))}", status_code=303)
    return RedirectResponse("/interviews?msg=Feedback+recorded", status_code=303)


@router.post("/interviews/add-candidate")
async def add_candidate(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    try:
        c = svc.add_candidate(db, actor, str(form.get("name") or ""), _int(form.get("demand_id")))
    except InterviewError as e:
        db.rollback()
        return RedirectResponse(f"/interviews?err={quote(str(e))}#find", status_code=303)
    return RedirectResponse(f"/interviews/record/{c.id}", status_code=303)


def _record_page(
    request: Request,
    actor: Actor,
    db: Session,
    c: Candidate,
    *,
    v: dict[str, Any] | None = None,
    error: str | None = None,
) -> HTMLResponse:
    account = db.get_one(Account, actor.account_id)
    d = db.get(Demand, c.demand_id) if c.demand_id else None
    same_name = [
        m
        for m in svc.search(db, actor.account_id, c.name)
        if m.candidate.name.casefold() == c.name.casefold()
    ]
    return render(
        request,
        "my_interviews/record.html",
        actor,
        db,
        status_code=400 if error else 200,
        c=c,
        d=d,
        same_name=same_name,
        requisitions=svc.open_requisitions(db, actor.account_id),
        history=svc.for_candidate(db, c.id),
        dims=account.settings.interview_ratings,
        v=v,
        error=error,
    )


@router.get("/interviews/record/{candidate_id}", response_class=HTMLResponse)
def record_page(
    candidate_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    c = db.get(Candidate, candidate_id)
    if c is None or c.account_id != actor.account_id:
        raise HTTPException(404, "Candidate not found.")
    return _record_page(request, actor, db, c)


@router.post("/interviews/record/{candidate_id}", response_class=HTMLResponse, response_model=None)
async def record(
    candidate_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    c = db.get(Candidate, candidate_id)
    if c is None or c.account_id != actor.account_id:
        raise HTTPException(404, "Candidate not found.")
    account = db.get_one(Account, actor.account_id)
    form = await request.form()
    fb = feedback_from_form(form, account.settings.interview_ratings)
    try:
        demand_id = _int(form.get("demand_id"))
        if demand_id and c.demand_id is None:
            c = svc.map_candidate(db, actor, c.id, demand_id)  # the panelist confirms the requisition
        svc.record_feedback(db, account, actor.id, c, fb)
    except InterviewError as e:
        db.rollback()
        v = {
            "round": fb.round,
            "ratings": fb.ratings,
            "outcome": fb.outcome,
            "comments": fb.comments,
            "needs_next_round": fb.needs_next_round,
            "next_round_note": fb.next_round_note,
        }
        return _record_page(request, actor, db, db.get_one(Candidate, candidate_id), v=v, error=str(e))
    msg = f"Recommendation for {c.name} recorded" + (
        " · another round requested" if fb.needs_next_round else ""
    )
    return RedirectResponse(f"/interviews?msg={quote(msg)}", status_code=303)
