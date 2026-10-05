"""Proactive, non-billable positions cost the account from their start date until marked billable."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.models import Demand, RateCard
from app.services import costing_service, loss_service
from app.services.loss_service import working_days
from tests.conftest import Client
from tests.test_charts import page_data

REF = "DM-000146"  # Neha's, sourcing profiles


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def proactive(db: Session, days_ago: int = 20) -> Demand:
    d = db.scalars(select(Demand).where(Demand.app_ref == REF)).one()
    d.category, d.position_type = "Proactive", "Non-billable"
    d.start_date = date.today() - timedelta(days=days_ago)
    db.commit()
    return d


def demand(db: Session) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == REF)).one()


def card_rate(db: Session, d: Demand) -> Decimal:
    rates = db.scalars(select(RateCard).where(RateCard.account_id == 1, RateCard.grade == d.grade)).all()
    assert rates, "the seed has a rate card entry for this grade"
    return max(r.cost_rate for r in rates if r.region == d.region and r.practice in (None, d.practice))


def test_costs_from_the_start_date_at_the_rate_card_rate(db: Session) -> None:
    d = proactive(db)
    today = date.today()
    cost = costing_service.costs(db, 1, today)[d.id]
    assert cost.active and cost.source == "rate card" and cost.rate is not None
    assert cost.working_days == working_days(d.start_date, today)  # no candidate has joined: it still costs
    assert cost.so_far == cost.rate * Decimal("8") * cost.working_days
    assert cost.monthly == cost.rate * Decimal("8") * costing_service.MONTH_DAYS
    # Never revenue lost.
    assert d.id not in {x.demand.id for x in loss_service.losses(db, 1, today)}


def test_not_before_the_start_date_and_not_for_other_positions(db: Session) -> None:
    d = proactive(db, days_ago=-5)  # starts next week
    assert d.id not in costing_service.costs(db, 1, date.today())
    d.start_date = date.today() - timedelta(days=10)
    d.category = "Open"  # a non-billable Open position isn't costed this way
    db.commit()
    assert d.id not in costing_service.costs(db, 1, date.today())


def test_owner_marks_it_billable_and_costing_stops(client: Client, db: Session) -> None:
    d = proactive(db)
    page = client.as_user("neha").get(f"/demands/{REF}").text
    assert "still costing the account" in page and "Mark as billable" in page
    assert "Mark as billable" not in client.as_user("kavya").get(f"/demands/{REF}").text  # the owner's switch

    when = date.today() - timedelta(days=6)
    r = client.as_user("kavya").post(f"/demands/{REF}/billable", data={"billable_from": when.isoformat()})
    assert "err=" in r.headers["location"]
    too_early = (d.start_date - timedelta(days=1)).isoformat()
    r = client.as_user("neha").post(f"/demands/{REF}/billable", data={"billable_from": too_early})
    assert "err=" in r.headers["location"] and demand(db).billable_from is None

    r = client.as_user("neha").post(f"/demands/{REF}/billable", data={"billable_from": when.isoformat()})
    assert "msg=" in r.headers["location"]
    d = demand(db)
    assert d.billable_from == when
    cost = costing_service.costs(db, 1, date.today())[d.id]
    assert not cost.active and cost.monthly is None and cost.working_days == working_days(d.start_date, when)
    assert any("Now billable" in m.subject and "kavya.r@example.com" in m.to for m in mail.sent)
    page = client.as_user("neha").get(f"/demands/{REF}").text
    assert "costing stopped" in page and "Undo: still non-billable" in page

    client.as_user("neha").post(f"/demands/{REF}/billable", data={"undo": "1"})
    assert demand(db).billable_from is None and costing_service.costs(db, 1, date.today())[d.id].active


def test_overview_carries_the_costing(client: Client, db: Session) -> None:
    d = proactive(db)
    html = client.as_user("sanjay").get("/overview").text
    data = page_data(html)
    row = next(r for r in data["rows"] if r["ref"] == REF)
    cost = costing_service.costs(db, 1, date.today())[d.id]
    assert row["costing"] == "Non-billable cost" and row["cost_active"] and row["cost_source"] == "rate card"
    assert row["cost"] == float(cost.so_far or 0) and row["cost_days"] == cost.working_days
    assert row["lost"] == 0 and not row["late"]  # a cost, not revenue lost
    assert sum(1 for r in data["rows"] if r["costing"]) == 1 and "caps" in data and data["month_days"] == 21


def test_a_joined_proactive_position_stays_in_view_until_billable(db: Session) -> None:
    from datetime import UTC, datetime

    from sqlalchemy import update

    from app.models import StageEvent
    from app.services.demand_service import record_stage

    d = proactive(db, days_ago=90)
    from app.core.enums import DemandStatus

    record_stage(db, d, DemandStatus.STAFFED, None)
    db.flush()
    old = datetime.now(UTC) - timedelta(days=60)
    db.execute(update(StageEvent).where(StageEvent.demand_id == d.id).values(at=old))
    db.execute(update(Demand).where(Demand.id == d.id).values(created_at=old - timedelta(days=30)))
    db.commit()
    today = date.today()
    assert d.id in {
        x.id for x in loss_service.overview(db, 1, today).demands
    }  # joined 60 days ago, still costing
    d = demand(db)
    d.billable_from = today - timedelta(days=45)
    db.commit()
    assert d.id not in {
        x.id for x in loss_service.overview(db, 1, today).demands
    }  # billable 45 days: archived
