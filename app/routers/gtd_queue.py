"""GTD team admin and GTD admin team: demands to enter on GTD, and linking the requisition IDs."""

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import Account
from app.services import gtd_service, notify_service
from app.services.gtd_service import GtdError

router = APIRouter(tags=["gtd queue"])
guard = require_screen("gtd_queue")


@router.get("/gtd-queue", response_class=HTMLResponse)
def queue_page(
    request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> HTMLResponse:
    q = gtd_service.queue(db, actor.account_id)
    acct = db.get_one(Account, actor.account_id)
    return render(
        request,
        "gtd_queue/index.html",
        actor,
        db,
        q=q,
        account=acct,
        failed_id=request.query_params.get("demand"),
        failed_value=request.query_params.get("value", ""),
    )


@router.post("/gtd-queue/{demand_id}/link")
async def link(
    demand_id: int, request: Request, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> RedirectResponse:
    raw = str((await request.form()).get("gtd_req_id", ""))
    try:
        sub = gtd_service.link_requisition(db, actor, demand_id, raw)
    except GtdError as e:
        return RedirectResponse(
            f"/gtd-queue?err={quote(str(e))}&demand={demand_id}&value={quote(raw)}#d{demand_id}",
            status_code=303,
        )
    return RedirectResponse(f"/gtd-queue?msg={quote(f'{sub.gtd_req_id} linked')}", status_code=303)


@router.get("/gtd-queue/export.csv")
def export(actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> Response:
    q = gtd_service.queue(db, actor.account_id)
    body = gtd_service.export_csv(q.in_mail + q.next_mail)
    return Response(
        body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="gtd-entry.csv"'},
    )


@router.post("/gtd-queue/send-mail")
def send_mail_now(actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> RedirectResponse:
    """Send the admin mail now instead of waiting for the account's mail time."""
    result = notify_service.send_daily_admin_mail(db, actor.account_id, force=True)
    key = "msg" if result.batch else "err"
    return RedirectResponse(f"/gtd-queue?{key}={quote(result.message)}", status_code=303)


@router.get("/api/gtd-queue")
def queue_json(actor: Actor = Depends(guard), db: Session = Depends(get_db)) -> dict[str, Any]:
    q = gtd_service.queue(db, actor.account_id)

    def row(d: Any) -> dict[str, Any]:
        return {"id": d.id, "app_ref": d.app_ref, "gtd_name": d.gtd_name, "status": d.status,
                "gtd_req_id": d.gtd_req_id, "business_unit": d.business_unit.name}  # fmt: skip

    return {
        "in_mail": [row(d) for d in q.in_mail],
        "next_mail": [row(d) for d in q.next_mail],
        "linked_today": [row(d) for d in q.linked_today],
    }
