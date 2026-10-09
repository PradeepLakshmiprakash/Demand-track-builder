"""GTD admin team lead and GTD admin team: upload the BCM sheet, see past imports."""

from datetime import date
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app.core import storage
from app.core.db import get_db
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import ExcelImport
from app.services import import_service, reconcile_service, trial_sheet_service
from app.services.demand_service import account_today
from app.services.import_service import SheetImportError

router = APIRouter(tags=["bcm sheet import"])
guard = require_screen("import")


def _page(
    request: Request, actor: Actor, db: Session, *, error: str | None = None, needs_confirm: bool = False,
    sheet_date: str | None = None,
) -> HTMLResponse:  # fmt: skip
    return render(
        request,
        "excel_import/index.html",
        actor,
        db,
        status_code=400 if error else 200,
        imports=import_service.history(db, actor.account_id),
        latest=reconcile_service.latest_import(db, actor.account_id),
        error=error,
        needs_confirm=needs_confirm,
        sheet_date=sheet_date or account_today(db, actor.account_id).isoformat(),
    )


@router.get("/imports", response_class=HTMLResponse)
def imports_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    return _page(request, actor, db)


@router.post("/imports", response_class=HTMLResponse, response_model=None)
async def upload(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse | RedirectResponse:
    form = await request.form()
    file = form.get("file")
    raw_date = str(form.get("sheet_date") or "")
    if not isinstance(file, UploadFile) or not file.filename:
        return _page(request, actor, db, error="Choose the BCM sheet file.", sheet_date=raw_date)
    filename = file.filename
    try:
        sheet_date = date.fromisoformat(raw_date) if raw_date else None
    except ValueError:
        sheet_date = None
    sheet_date = sheet_date or import_service.date_from_filename(filename)
    if sheet_date is None:
        return _page(request, actor, db, error="Enter the date the sheet is from.", sheet_date=raw_date)
    try:
        result = import_service.import_sheet(
            db, actor, filename, await file.read(), sheet_date, confirm_older=form.get("confirm_older") == "1"
        )
    except SheetImportError as e:
        db.rollback()
        return _page(
            request, actor, db, error=str(e), needs_confirm=e.needs_confirm, sheet_date=sheet_date.isoformat()
        )
    s = result.summary
    msg = (
        f"Imported {s.rows} rows: {s.in_sheet} in sheet, {len(s.missing)} missing, {s.needs_person} to match"
    )
    return RedirectResponse(f"/reconciliation?msg={quote(msg)}", status_code=303)


@router.get("/imports/trial-sheet")
def trial_sheet(actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> Response:
    """A sheet to try the import with, built from the account's demands as they are now."""
    sheet = trial_sheet_service.build(db, actor.account_id, account_today(db, actor.account_id))
    return Response(
        sheet.data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{sheet.filename}"'},
    )


@router.get("/imports/{import_id}/file")
def download(import_id: int, actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> FileResponse:
    imp = db.get(ExcelImport, import_id)
    if imp is None or imp.account_id != actor.account_id:
        raise HTTPException(404, "Import not found.")
    try:
        path = storage.open_path(imp.file_path)
    except FileNotFoundError as e:
        raise HTTPException(404, "The uploaded file is missing.") from e
    return FileResponse(path, filename=imp.file_name)


@router.post("/imports/{import_id}/rerun")
def rerun(import_id: int, actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> RedirectResponse:
    imp = db.get(ExcelImport, import_id)
    if imp is None or imp.account_id != actor.account_id:
        raise HTTPException(404, "Import not found.")
    try:
        reconcile_service.reconcile(db, imp, actor.id)
    except reconcile_service.ReconcileError as e:
        return RedirectResponse(f"/reconciliation?import={import_id}&err={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/reconciliation?import={import_id}&msg=Reconciliation+re-run", status_code=303)
