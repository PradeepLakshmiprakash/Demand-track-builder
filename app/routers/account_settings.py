"""Admin: per-account thresholds, BU list, practices/grades, supply channels and BCM sheet status mapping."""

from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.account_config import DP_FIELD_LABELS, DP_FIELDS
from app.core.db import get_db
from app.core.enums import SHEET_STAGES, EscalationType, Responsible, Severity
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.services import account_service as svc
from app.services.account_service import SettingsError

router = APIRouter(tags=["account settings"])
guard = require_screen("settings")


def _done(section: str, action: Callable[[], None], db: Session) -> RedirectResponse:
    try:
        action()
    except SettingsError as e:
        db.rollback()
        return RedirectResponse(f"/settings?err={quote(str(e))}#{section}", status_code=303)
    return RedirectResponse(f"/settings?msg=Saved#{section}", status_code=303)


def _rows(form: dict[str, list[str]], *fields: str) -> list[dict[str, str]]:
    """Zip parallel form arrays (status[], stage[], …) back into rows."""
    cols = [form.get(f, []) for f in fields]
    n = max((len(c) for c in cols), default=0)
    return [{f: (cols[i][j] if j < len(cols[i]) else "") for i, f in enumerate(fields)} for j in range(n)]


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    account = svc.get_account(db, actor.account_id)
    return render(
        request,
        "account_settings/index.html",
        actor,
        db,
        account=account,
        cfg=account.settings,
        esc_types=list(EscalationType),
        responsibles=list(Responsible),
        severities=list(Severity),
        bus=[(b, *svc.bu_usage(db, b.id)) for b in account.business_units],
        stages=SHEET_STAGES,
        dp_fields=[(f, DP_FIELD_LABELS[f], req) for f, (_, req) in DP_FIELDS.items()],
    )


@router.post("/settings/thresholds")
async def save_thresholds(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    data = {k: str(v) for k, v in (await request.form()).items()}
    return _done("thresholds", lambda: svc.update_thresholds(db, actor.account_id, data), db)


@router.post("/settings/lists")
async def save_lists(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    lists = {k: str(v).splitlines() for k, v in form.items()}
    return _done("lists", lambda: svc.update_lists(db, actor.account_id, lists), db)


@router.post("/settings/business-units")
async def add_bu(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    name = str((await request.form()).get("name", ""))
    return _done("business-units", lambda: svc.add_business_unit(db, actor.account_id, name), db)


@router.post("/settings/business-units/{bu_id}")
async def edit_bu(
    bu_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    name, active = str(form.get("name", "")), form.get("active") == "1"
    head, email = str(form.get("head_name") or ""), str(form.get("head_email") or "")
    return _done(
        "business-units",
        lambda: svc.update_business_unit(db, actor.account_id, bu_id, name, active, head, email),
        db,
    )


@router.post("/settings/status-mapping")
async def save_mapping(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    raw = {k: [str(v) for v in form.getlist(k)] for k in ("status_group", "status", "stage")}
    rows = _rows(raw, "status_group", "status", "stage")
    return _done("status-mapping", lambda: svc.update_status_mapping(db, actor.account_id, rows), db)


@router.post("/settings/supply-channels")
async def save_channels(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    raw = {k: [str(v) for v in form.getlist(k)] for k in ("label", "sheet_marker", "needs_sourcing_req")}
    rows = _rows(raw, "label", "sheet_marker", "needs_sourcing_req")
    return _done("supply-channels", lambda: svc.update_supply_channels(db, actor.account_id, rows), db)


@router.post("/settings/escalation-rules")
async def save_escalation_rules(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = {k: str(v) for k, v in (await request.form()).items()}
    return _done("escalation-rules", lambda: svc.update_escalation_rules(db, actor.account_id, form), db)


@router.post("/settings/dp-columns")
async def save_dp_columns(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    headers = {f: str(form.get(f"col_{f}") or "") for f in DP_FIELDS}
    blanks = str(form.get("blank_values") or "").split(",")
    return _done("dp-columns", lambda: svc.update_dp_columns(db, actor.account_id, headers, blanks), db)
