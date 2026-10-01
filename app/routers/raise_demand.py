"""Demand owner (and GTD team admin): raise a demand, edit it while it's a draft or waiting for the
admin mail, submit it, attach the job description."""

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core.db import get_db
from app.core.enums import Role
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import Account, BusinessUnit, Demand
from app.schemas.raise_demand import DemandForm
from app.services import demand_service as svc
from app.services.demand_service import DemandError

router = APIRouter(tags=["raise demand"])
guard = require_screen("raise")

FIELDS = (
    "bu_id", "name", "practice", "grade", "category", "type", "replaced_resource", "lwd", "position_type",
    "client_interview_required",
    "primary_skills", "secondary_skills", "exp_min", "exp_max", "client_rate", "start_date", "region",
    "location", "work_mode", "hiring_manager", "positions",
)  # fmt: skip


def _values(demand: Demand | None, actor: Actor) -> dict[str, Any]:
    if demand is None:
        return {
            "category": "Open",
            "type": "New",
            "position_type": "Billable",
            "client_interview_required": True,
            "positions": 1,
            "region": "US",
        }
    v = {f: getattr(demand, f, None) for f in FIELDS if f != "positions"}
    v["primary_skills"] = ", ".join(demand.primary_skills)
    v["secondary_skills"] = ", ".join(demand.secondary_skills)
    if not actor.can_see_bill_rate:
        v["client_rate"] = None  # write-only for demand owners
    return v


def _page(
    request: Request, actor: Actor, db: Session, *, demand: Demand | None, values: dict[str, Any],
    error: str | None = None,
) -> HTMLResponse:  # fmt: skip
    account = db.get_one(Account, actor.account_id)
    bus = list(
        db.scalars(
            select(BusinessUnit)
            .where(BusinessUnit.account_id == actor.account_id, BusinessUnit.active)
            .order_by(BusinessUnit.id)
        )
    )
    return render(
        request,
        "raise_demand/form.html",
        actor,
        db,
        status_code=400 if error else 200,
        demand=demand,
        v=values,
        error=error,
        cfg=account.settings,
        account=account,
        bus=bus,
        pick_bu=actor.role is not Role.DEMAND_OWNER,
        rate_on_file=bool(demand and demand.client_rate is not None),
    )


def _message(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(
            f"{str(err['loc'][0]).replace('_', ' ').capitalize() + ': ' if err['loc'] else ''}{err['msg']}"
            for err in e.errors()
        )
    return str(e)


async def _read(request: Request) -> tuple[dict[str, Any], bool, svc.Upload | None]:
    form = await request.form()
    raw = {f: form.get(f) for f in FIELDS}
    jd = form.get("jd")
    upload = (jd.filename, await jd.read()) if isinstance(jd, UploadFile) and jd.filename else None
    return raw, form.get("action") == "submit", upload


def _editable(db: Session, actor: Actor, ref: str) -> Demand:
    demand = svc.get_visible(db, actor, ref)
    if demand is None:
        raise HTTPException(404, "Demand not found.")
    if not svc.can_change(actor, demand):
        raise HTTPException(403, "This demand can't be changed any more.")
    return demand


@router.get("/demands/new", response_class=HTMLResponse)
def new_page(request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> HTMLResponse:
    return _page(request, actor, db, demand=None, values=_values(None, actor))


@router.post("/demands/new", response_class=HTMLResponse, response_model=None)
async def create(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    raw, submit, upload = await _read(request)
    try:
        created = svc.create_demands(db, actor, DemandForm.model_validate(raw), submit=submit, jd=upload)
    except (ValidationError, DemandError) as e:
        db.rollback()
        return _page(request, actor, db, demand=None, values=raw, error=_message(e))
    first, last = created[0].app_ref, created[-1].app_ref
    what = first if len(created) == 1 else f"{len(created)} demands, {first} to {last},"
    msg = f"{what} {'submitted: they go out in the next admin mail' if submit else 'saved as draft'}"
    return RedirectResponse(f"/demands/{first}?msg={quote(msg)}", status_code=303)


@router.get("/demands/{ref}/edit", response_class=HTMLResponse)
def edit_page(
    ref: str, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    demand = _editable(db, actor, ref)
    return _page(request, actor, db, demand=demand, values=_values(demand, actor))


@router.post("/demands/{ref}/edit", response_class=HTMLResponse, response_model=None)
async def update(
    ref: str, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    demand = _editable(db, actor, ref)
    raw, submit, upload = await _read(request)
    try:
        svc.update_demand(db, actor, demand, DemandForm.model_validate(raw), submit=submit, jd=upload)
    except (ValidationError, DemandError) as e:
        db.rollback()
        return _page(request, actor, db, demand=demand, values=raw, error=_message(e))
    msg = "Submitted: it goes out in the next admin mail" if submit else "Changes saved"
    return RedirectResponse(f"/demands/{ref}?msg={quote(msg)}", status_code=303)


@router.post("/demands/{ref}/submit")
def submit(ref: str, actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> RedirectResponse:
    """Submit a saved draft as it stands (the detail page's button)."""
    demand = _editable(db, actor, ref)
    values = _values(demand, actor) | {"client_rate": None}  # None keeps the rate on file
    try:
        svc.update_demand(db, actor, demand, DemandForm.model_validate(values), submit=True)
    except (ValidationError, DemandError) as e:
        db.rollback()
        return RedirectResponse(f"/demands/{ref}/edit?err={quote(_message(e))}", status_code=303)
    return RedirectResponse(
        f"/demands/{ref}?msg={quote('Submitted: it goes out in the next admin mail')}", status_code=303
    )
