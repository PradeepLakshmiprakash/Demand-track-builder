"""What the demand owner sees after panel selection: the offer approval's progress (never its
rates or margin), the date of joining, and each candidate's stage kept up to date by the sheet."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.models import Candidate, Demand, GtdSubmission, User
from app.services import margin_service
from tests.conftest import Client, user_id
from tests.test_commercial import SHEET_DAY, actor, by_ref, import_sample, offer_for


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def by_req(db: Session, req: str) -> Demand:
    db.expire_all()
    sub = db.scalars(select(GtdSubmission).where(GtdSubmission.gtd_req_id == req)).one()
    return db.get_one(Demand, sub.demand_id)


def owner_of(db: Session, ref: str) -> User:
    return db.get_one(User, by_ref(db, ref).owner_id)


def test_owner_sees_where_the_offer_is_without_the_numbers(client: Client, db: Session) -> None:
    import_sample(client)
    owner = owner_of(db, "DM-000131")  # B1 Sogeti, 43.75%: with the GTD team admin
    assert owner.id == user_id("rahul")
    text = client.as_user("rahul").get("/demands/DM-000131").text
    assert "Offer approval" in text and "Waiting for approval" in text and "with the GTD team admin" in text
    assert "margin 4" not in text and "43.8" not in text and "$45" not in text

    admin_view = client.as_user("kavya").get("/demands/DM-000131").text
    assert "margin 43.8%" in admin_view


def test_decision_mails_the_owner_and_shows_on_the_demand(client: Client, db: Session) -> None:
    import_sample(client)
    owner = owner_of(db, "DM-000131")
    margin_service.decide(db, actor(db, "kavya"), offer_for(db, "DM-000131").id, "approved", None)
    [m] = [m for m in mail.sent if m.to == [owner.email]]
    assert "Offer for Candidate B approved" in m.subject and "Kavya" in m.text
    assert "%" not in m.text and "$" not in m.text  # owners don't see rates or margin
    cand = db.scalars(select(Candidate).where(Candidate.demand_id == by_ref(db, "DM-000131").id)).one()
    assert cand.current_stage == "Offer approved"

    # The next sheet still says "Offer in Process": the approved stage stays.
    import_sample(client, SHEET_DAY + timedelta(days=7), title=False)
    db.expire_all()
    assert db.get_one(Candidate, cand.id).current_stage == "Offer approved"
    page = client.as_user("kavya").get("/demands/DM-000131").text
    assert "Approved" in page and "by Kavya" in page


def test_decline_reason_reaches_the_owner(client: Client, db: Session) -> None:
    import_sample(client)
    owner = owner_of(db, "DM-000131")
    margin_service.decide(
        db,
        actor(db, "kavya"),
        offer_for(db, "DM-000131").id,
        "declined",
        "Candidate asked for more than budget",
    )
    [m] = [m for m in mail.sent if m.to == [owner.email]]
    assert "declined" in m.subject and "Candidate asked for more than budget" in m.text


def test_date_of_joining_and_candidate_stage_come_from_the_sheet(client: Client, db: Session) -> None:
    import_sample(client)
    d = by_req(db, "IXT3SF")  # Offer in Market, DOJ 15 Oct 2026, Candidate D
    assert d.status == "offer_in_market"
    page = client.as_user("kavya").get(f"/demands/{d.app_ref}").text
    assert "Date of joining" in page and "15 Oct 2026" in page
    assert f"{(date(2026, 10, 15) - date(2026, 9, 1)).days} days after the requested start" in page
    cand = db.scalars(
        select(Candidate).where(Candidate.demand_id == d.id, Candidate.name == "Candidate D")
    ).one()
    assert cand.current_stage == "Offer made, joining awaited"
