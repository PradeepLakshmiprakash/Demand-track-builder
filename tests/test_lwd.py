"""Replacement demands: the leaver's last working day (LWD) and where revenue loss starts."""

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Demand
from app.services import loss_service
from tests.conftest import Client

TODAY = date(2026, 9, 23)


def demand(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def _form(**kw: str) -> dict[str, str]:
    base = {
        "name": "Java Developer", "practice": "Cloud-Java", "grade": "C1", "category": "Open",
        "type": "Replacement", "replaced_resource": "A. Leaver", "position_type": "Billable",
        "primary_skills": "Java", "start_date": (date.today() + timedelta(days=30)).isoformat(),
        "region": "US", "location": "Chicago", "work_mode": "Hybrid", "action": "submit",
    }  # fmt: skip
    return {**base, **kw}


def test_replacement_needs_the_last_working_day(client: Client, db: Session) -> None:
    r = client.as_user("priya").post("/demands/new", data=_form())
    assert r.status_code == 400 and "Their last working day" in r.text
    lwd = (date.today() + timedelta(days=20)).isoformat()
    r = client.post("/demands/new", data=_form(lwd=lwd))
    assert r.status_code == 303, r.text
    ref = r.headers["location"].split("/")[2].split("?")[0]
    d = demand(db, ref)
    assert d.lwd is not None and d.practice == "Cloud-Java" and d.gtd_name == "Java Developer"
    page = client.get(f"/demands/{ref}").text
    assert "Last working day" in page and "9 days uncovered before the requested start" in page


def test_a_new_position_keeps_no_lwd(client: Client, db: Session) -> None:
    r = client.as_user("priya").post("/demands/new", data=_form(type="New", lwd="2026-10-01"))
    ref = r.headers["location"].split("/")[2].split("?")[0]
    assert demand(db, ref).lwd is None


def test_loss_counts_from_the_start_date_unless_the_leaver_goes_later(db: Session) -> None:
    d = demand(db, "DM-000117")  # start 1 Sep 2026, bill 88/h, no DOJ
    before = next(x for x in loss_service.losses(db, 1, TODAY) if x.demand.id == d.id)
    assert before.working_days_late == 16 and before.lost == Decimal("88") * 8 * 16

    d.type, d.lwd = "Replacement", date(2026, 8, 20)  # left before the start: loss still from the start
    db.commit()
    same = next(x for x in loss_service.losses(db, 1, TODAY) if x.demand.id == d.id)
    assert same.working_days_late == 16 and demand(db, "DM-000117").uncovered_days == 11

    d = demand(db, "DM-000117")
    d.lwd = date(2026, 9, 10)  # still billing until 10 Sep: loss from 11 Sep
    db.commit()
    later = next(x for x in loss_service.losses(db, 1, TODAY) if x.demand.id == d.id)
    assert demand(db, "DM-000117").loss_from == date(2026, 9, 11)
    assert later.working_days_late == 8 and later.lost == Decimal("88") * 8 * 8


def test_no_loss_while_the_leaver_is_still_there(db: Session) -> None:
    d = demand(db, "DM-000117")
    d.type, d.lwd = "Replacement", date(2026, 9, 30)
    db.commit()
    assert all(x.demand.id != d.id for x in loss_service.losses(db, 1, TODAY))
