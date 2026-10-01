"""GTD team admin: interviewer skill tags. Alerts go to interviewers sharing at least one
technology with a requisition, so the tags are what routes requisitions to people."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import InterviewStatus, Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import Account, Interview, InterviewerProfile, User, member_of
from app.schemas.user_access import _split
from app.services import interview_service as svc

router = APIRouter(tags=["interviewer profiles"])
guard = require_screen("interviewer_profiles")


@router.get("/interviewers", response_class=HTMLResponse)
def profiles_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    account = db.get_one(Account, actor.account_id)
    rows = db.execute(
        select(User, InterviewerProfile)
        .outerjoin(InterviewerProfile, InterviewerProfile.user_id == User.id)
        .where(member_of(actor.account_id, Role.INTERVIEWER, active=False))
        .order_by(User.active.desc(), User.name)
    ).all()
    reqs = svc.open_requisitions(db, actor.account_id)
    out = []
    for u, p in rows:
        skills = {s.casefold() for s in (p.skills if p else [])}
        matching = [d for d in reqs if skills & {s.casefold() for s in d.primary_skills + d.secondary_skills}]
        load = len(
            list(
                db.scalars(
                    select(Interview.id).where(
                        Interview.interviewer_id == u.id, Interview.status == InterviewStatus.SCHEDULED.value
                    )
                )
            )
        )
        out.append((u, p, matching, load))
    return render(request, "interviewer_profiles/index.html", actor, db, rows=out, cfg=account.settings)


@router.post("/interviewers/{user_id}")
async def save(
    user_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    u = db.scalar(
        select(User).where(User.id == user_id, member_of(actor.account_id, Role.INTERVIEWER, active=False))
    )
    if u is None:
        return RedirectResponse("/interviewers?err=Interviewer+not+found", status_code=303)
    form = await request.form()
    cfg = db.get_one(Account, actor.account_id).settings
    grade = str(form.get("max_grade") or "") or None
    if grade and grade not in cfg.grades:
        return RedirectResponse("/interviewers?err=Unknown+grade", status_code=303)
    p = db.get(InterviewerProfile, u.id) or InterviewerProfile(user_id=u.id)
    p.skills = _split(str(form.get("skills") or ""))
    p.practices = [x for x in form.getlist("practices") if isinstance(x, str) and x in cfg.practices]
    p.max_grade = grade
    p.active = form.get("active") == "1"
    db.add(p)
    db.commit()
    return RedirectResponse(f"/interviewers?msg={quote(f'Saved {u.name}')}#u{u.id}", status_code=303)
