"""Demands list: "My demands" for demand owners, "All demands" for full-account roles."""

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.enums import Scope
from app.core.security import Actor, require_screen
from app.core.templating import render
from app.models import BusinessUnit
from app.services.demand_service import FILTERS, demand_rows, filter_counts, filter_rows, summary

router = APIRouter(tags=["demands"])
guard = require_screen("demands")


@router.get("/demands", response_class=HTMLResponse)
def demands_page(
    request: Request,
    filter: str = "all",
    bu: int | None = None,
    actor: Actor = Depends(guard),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if filter not in FILTERS:
        filter = "all"
    all_rows = demand_rows(db, actor)
    # BU filter: only the BUs this actor can actually see, and only when there's more than one.
    bus_stmt = (
        select(BusinessUnit).where(BusinessUnit.account_id == actor.account_id).order_by(BusinessUnit.id)
    )
    if not actor.is_full:
        bus_stmt = bus_stmt.where(BusinessUnit.id.in_(actor.bu_ids))
    bus = list(db.scalars(bus_stmt)) if actor.scope in (Scope.FULL, Scope.OWN_BU_READ) else []
    if len(bus) < 2:
        bus = []
    return render(
        request,
        "my_demands/index.html",
        actor,
        db,
        rows=filter_rows(all_rows, filter, bu),
        counts=filter_counts(filter_rows(all_rows, "all", bu)),
        filters=FILTERS,
        active_filter=filter,
        bus=bus,
        active_bu=bu,
        stats=summary(all_rows),
        title="My demands" if actor.scope is not Scope.FULL else "All demands",
    )


@router.get("/api/demands")
def demands_json(
    filter: str = "all", bu: int | None = None, actor: Actor = Depends(guard), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = filter_rows(demand_rows(db, actor), filter if filter in FILTERS else "all", bu)
    out = []
    for r in rows:
        d = r.demand
        item: dict[str, Any] = {
            "app_ref": d.app_ref,
            "gtd_req_id": r.req_id,
            "name": d.name,
            "business_unit": d.business_unit.name,
            "practice": d.practice,
            "grade": d.grade,
            "start_date": d.start_date,
            "status": d.status,
            "status_label": r.status_label,
            "needs_attention": r.attention,
            "owner": d.owner.name,
            "read_only": r.read_only,
        }
        if actor.can_see_bill_rate:
            item["client_rate"] = d.client_rate
        out.append(item)
    return out
