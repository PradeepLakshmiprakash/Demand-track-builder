"""The interviewer's feedback link from the invite mail. Works without signing in; single use: the
token is cleared when the feedback is recorded. Also serves that candidate's CV while it's valid."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core import storage
from app.core.db import get_db
from app.core.templating import templates
from app.models import Account, Candidate, Demand, Interview, User
from app.schemas.interviews import feedback_from_form
from app.services import interview_service as svc
from app.services.interview_service import InterviewError

router = APIRouter(tags=["feedback link"])


def _live(db: Session, token: str) -> Interview:
    iv = svc.by_token(db, token)
    if iv is None:
        raise HTTPException(404, "This feedback link has been used or is no longer valid.")
    return iv


def _page(request: Request, db: Session, iv: Interview, error: str | None = None) -> HTMLResponse:
    c = db.get_one(Candidate, iv.candidate_id)
    d = db.get(Demand, iv.demand_id) if iv.demand_id else None
    account = db.get_one(Account, c.account_id)
    return templates.TemplateResponse(
        request,
        "feedback/form.html",
        {
            "iv": iv,
            "c": c,
            "d": d,
            "who": db.get(User, iv.interviewer_id),
            "dims": account.settings.interview_ratings,
            "fbref": svc.form_reference(db, account),
            "error": error,
            "token": iv.feedback_token,
        },
        status_code=400 if error else 200,
    )


@router.get("/feedback/{token}", response_class=HTMLResponse)
def feedback_page(token: str, request: Request, db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        return _page(request, db, _live(db, token))
    except HTTPException:
        return templates.TemplateResponse(request, "feedback/done.html", {"used": True}, status_code=404)


@router.post("/feedback/{token}", response_class=HTMLResponse, response_model=None)
async def submit(
    token: str, request: Request, db: Session = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    try:
        iv = _live(db, token)
    except HTTPException:
        return templates.TemplateResponse(request, "feedback/done.html", {"used": True}, status_code=404)
    c = db.get_one(Candidate, iv.candidate_id)
    account = db.get_one(Account, c.account_id)
    try:
        svc.record_feedback(
            db,
            account,
            iv.interviewer_id or 0,
            c,
            feedback_from_form(await request.form(), account.settings.interview_ratings, iv.round),
            iv,
        )
    except InterviewError as e:
        db.rollback()
        return _page(request, db, _live(db, token), error=str(e))
    return RedirectResponse(f"/feedback-done?name={quote(c.name)}", status_code=303)


@router.get("/feedback-done", response_class=HTMLResponse, include_in_schema=False)
def done(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "feedback/done.html", {"name": request.query_params.get("name")}
    )


@router.get("/cv/{token}")
def cv(token: str, db: Session = Depends(get_db)) -> FileResponse:
    iv = _live(db, token)
    c = db.get_one(Candidate, iv.candidate_id)
    if not c.cv_path:
        raise HTTPException(404, "No CV attached yet.")
    try:
        path = storage.open_path(c.cv_path)
    except FileNotFoundError as e:
        raise HTTPException(404, "The CV file is missing.") from e
    return FileResponse(path, filename=path.name)
