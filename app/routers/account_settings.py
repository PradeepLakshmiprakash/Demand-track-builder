"""Admin: per-account thresholds, BU list, practices/grades, supply channels and BCM sheet status mapping."""

from collections.abc import Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app.core.account_config import DP_FIELD_LABELS, DP_FIELDS
from app.core.db import get_db
from app.core.enums import SHEET_STAGES, DemandStatus, EscalationType, Responsible, Severity
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.services import account_service as svc
from app.services import housekeeping_service
from app.services.account_service import SettingsError

# What the speed report can have a target for: the whole journey, each step that takes time, and the wait
# between joining and billing.
TARGET_STEPS: list[tuple[str, str]] = [
    ("time_to_fill", "Time to fill"),
    *[
        (s.value, s.label)
        for s in (
            DemandStatus.SUBMITTED,
            DemandStatus.SENT_TO_GTD,
            DemandStatus.COVERAGE_REQUIRED,
            DemandStatus.INTERVIEWING,
            DemandStatus.PROFILES_WITH_CLIENT,
            DemandStatus.OFFER_IN_PROCESS,
            DemandStatus.OFFER_IN_MARKET,
        )
    ],
    ("joined_to_billing", "Joined to billing"),
]

router = APIRouter(tags=["account settings"])
guard = require_screen("settings")


def _done(section: str, action: Callable[[], None], db: Session) -> RedirectResponse:
    try:
        action()
    except SettingsError as e:
        db.rollback()
        return RedirectResponse(f"/settings?err={quote(str(e))}#{section}", status_code=303)
    except StaleDataError:  # someone else saved the account's settings in the same moment
        db.rollback()
        msg = "Someone else saved settings at the same moment. Yours was not saved: check and save again."
        return RedirectResponse(f"/settings?err={quote(msg)}#{section}", status_code=303)
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
        people=svc.people(db, actor.account_id),
        target_steps=TARGET_STEPS,
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


@router.post("/settings/practice-stacks")
async def save_practice_stacks(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    names = [str(v) for v in form.getlist("practice")]
    lines = [str(v) for v in form.getlist("stacks")]
    stacks = dict(zip(names, lines, strict=False))
    return _done("practice-stacks", lambda: svc.update_practice_stacks(db, actor.account_id, stacks), db)


@router.post("/settings/rename")
async def rename_value(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    """Rename a practice or a grade: every demand, rate, person and profile that carries it follows."""
    f = await request.form()
    kind, old, new = str(f.get("kind") or ""), str(f.get("old") or ""), str(f.get("new") or "")
    try:
        used = housekeeping_service.rename(db, actor.account_id, kind, old, new)
    except housekeeping_service.HousekeepingError as e:
        db.rollback()
        return RedirectResponse(f"/settings?err={quote(str(e))}#lists", status_code=303)
    msg = (
        f"{old} is now {new.strip()}: {used} demand{'' if used == 1 else 's'} updated, with rates and people"
    )
    return RedirectResponse(f"/settings?msg={quote(msg)}#lists", status_code=303)


@router.post("/settings/retention")
async def save_retention(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = {k: str(v) for k, v in (await request.form()).items()}
    return _done("retention", lambda: svc.update_retention(db, actor.account_id, form), db)


@router.post("/settings/onboarding")
async def save_onboarding(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    form = await request.form()
    n = len(form.getlist("label"))
    cols = ("key", "label", "owner_kind", "person_id", "outside_label", "due_days_before")
    raw = {k: [str(v) for v in form.getlist(k)] for k in cols}
    rows = _rows(raw, *cols)
    on = {str(v) for v in form.getlist("enabled")}  # checkboxes post their row number when ticked
    esc = {str(v) for v in form.getlist("escalate")}
    for i, row in enumerate(rows[:n]):
        row["enabled"], row["escalate"] = ("1" if str(i) in on else ""), ("1" if str(i) in esc else "")
    flat = {k: str(v) for k, v in form.items() if k not in (*cols, "enabled", "escalate")}
    return _done("onboarding", lambda: svc.update_onboarding(db, actor.account_id, rows, flat), db)


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
