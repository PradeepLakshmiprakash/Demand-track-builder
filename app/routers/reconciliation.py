"""Lead admin and GTD admin team: what the latest BCM sheet says, and rows that need a person."""

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.db import get_db
from app.core.enums import FINISHED, DemandStatus, Role, RowOutcome
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import BusinessUnit, Demand, ExcelImport, User, member_of
from app.services import escalation_service, import_service, reconcile_service
from app.services.reconcile_service import ReconcileError

router = APIRouter(tags=["reconciliation"])
guard = require_screen("reconciliation")


@router.get("/reconciliation", response_class=HTMLResponse)
def reconciliation_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    import_id = _int(request.query_params.get("import"))
    latest = reconcile_service.latest_import(db, actor.account_id)
    imp = db.get(ExcelImport, import_id) if import_id else latest
    if imp is not None and imp.account_id != actor.account_id:
        raise HTTPException(404, "Import not found.")
    ctx: dict[str, Any] = {
        "imp": imp,
        "latest": latest,
        "imports": import_service.history(db, actor.account_id),
    }
    if imp is not None:
        rows = reconcile_service.rows_of(db, imp)
        s = reconcile_service.summary_of(imp)
        refs = set(s.get("missing", []) + s.get("dropped", []) + s.get("incorrect", []) + s.get("prefix", []))
        refs |= {c["ref"] for c in s.get("stage_changes", [])}
        demands = {
            d.app_ref: d
            for d in db.scalars(
                select(Demand)
                .where(Demand.account_id == actor.account_id, Demand.app_ref.in_(refs))
                .options(selectinload(Demand.submissions), selectinload(Demand.owner))
            )
        }
        escs = escalation_service.open_for(db, [d.id for d in demands.values()])
        todo = [r for r in rows if RowOutcome(r.outcome).needs_person]
        owners = list(
            db.scalars(
                select(User)
                .where(member_of(actor.account_id, Role.DEMAND_OWNER, Role.ADMIN))
                .options(selectinload(User.business_units))
                .order_by(User.name)
            )
        )  # fmt: skip
        in_sheet_ids = {r.submission_id for r in rows if r.submission_id}
        open_demands = [
            d
            for d in db.scalars(
                select(Demand)
                .where(Demand.account_id == actor.account_id,
                       Demand.status.notin_([s.value for s in (*FINISHED, DemandStatus.DRAFT)]))
                .options(selectinload(Demand.submissions))
                .order_by(Demand.app_ref)
            )
            if not any(sub.id in in_sheet_ids for sub in d.submissions)
        ]  # fmt: skip
        ctx |= {
            "s": s,
            "rows": rows,
            "todo": todo,
            "demands": demands,
            "escs": escs,
            "owners": owners,
            "owner_guess": {
                r.id: reconcile_service.owner_for_originator(db, actor.account_id, r.originator) for r in todo
            },  # fmt: skip
            "bus": list(
                db.scalars(
                    select(BusinessUnit).where(
                        BusinessUnit.account_id == actor.account_id, BusinessUnit.active
                    )
                )
            ),  # fmt: skip
            "open_demands": open_demands,
            "is_latest": latest is not None and imp.id == latest.id,
            "outcomes": RowOutcome,
        }
    return render(request, "reconciliation/index.html", actor, db, **ctx)


def _int(v: str | None) -> int | None:
    return int(v) if v and v.isdigit() else None


def _back(row_id: int, e: Exception | None = None, msg: str | None = None) -> RedirectResponse:
    q = f"err={quote(str(e))}#row{row_id}" if e else f"msg={quote(msg or 'Saved')}"
    return RedirectResponse(f"/reconciliation?{q}", status_code=303)


@router.post("/reconciliation/rows/{row_id}/confirm")
async def confirm(
    row_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    demand_id = _int(str((await request.form()).get("demand_id") or ""))
    if demand_id is None:
        return _back(row_id, ReconcileError("Pick the demand this row belongs to."))
    try:
        d = reconcile_service.confirm_match(db, actor.account_id, actor.id, row_id, demand_id)
    except ReconcileError as e:
        db.rollback()
        return _back(row_id, e)
    return _back(row_id, msg=f"Row linked to {d.app_ref}")


@router.post("/reconciliation/rows/{row_id}/create")
async def create(
    row_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    owner_id, bu_id = _int(str(form.get("owner_id") or "")), _int(str(form.get("bu_id") or ""))
    if owner_id is None:
        return _back(row_id, ReconcileError("Pick the demand owner."))
    try:
        d = reconcile_service.create_from_row(db, actor.account_id, actor.id, row_id, owner_id, bu_id)
    except ReconcileError as e:
        db.rollback()
        return _back(row_id, e)
    return _back(row_id, msg=f"{d.app_ref} created from the row and linked")
