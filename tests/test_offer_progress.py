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


def test_owner_sees_the_offer_with_its_numbers_and_can_decide(client: Client, db: Session) -> None:
    import_sample(client)
    owner = owner_of(db, "DM-000131")  # B1 Sogeti, 43.75%: the demand owner decides
    assert owner.id == user_id("rahul")
    text = client.as_user("rahul").get("/demands/DM-000131").text
    assert "Offer approval" in text and "Waiting for approval" in text and "with the demand owner" in text
    assert "margin 43.8%" in text and "$45.00" in text and ">Decide</a>" in text
    # the GTD admin team sees where it stands, never the numbers
    team = client.as_user("farah").get("/demands/DM-000131").text
    assert "Waiting for approval" in team and "43.8" not in team and "$45" not in team
    assert "margin 43.8%" in client.as_user("kavya").get("/demands/DM-000131").text


def test_owner_approves_and_the_gtd_team_admin_is_notified(client: Client, db: Session) -> None:
    import_sample(client)
    mail.sent.clear()
    margin_service.decide(db, actor(db, "rahul"), offer_for(db, "DM-000131").id, "approved", None)
    [m] = mail.sent
    assert m.to == ["kavya.r@example.com"]  # notified; the owner decided, so isn't mailed his own decision
    assert "Offer for Candidate B approved" in m.subject and "by Rahul K" in m.text and "43.75%" in m.text
    assert "records the expected date of joining" in m.text
    cand = db.scalars(select(Candidate).where(Candidate.demand_id == by_ref(db, "DM-000131").id)).one()
    assert cand.current_stage == "Offer approved"

    # The next sheet still says "Offer in Process": the approved stage stays.
    import_sample(client, SHEET_DAY + timedelta(days=7), title=False)
    db.expire_all()
    assert db.get_one(Candidate, cand.id).current_stage == "Offer approved"
    page = client.as_user("kavya").get("/demands/DM-000131").text
    assert "Approved" in page and "by Rahul" in page


def test_a_new_offer_mails_whoever_decides_it(client: Client, db: Session) -> None:
    mail.sent.clear()
    import_sample(client)
    raised = {m.subject: m for m in mail.sent if "Offer approval needed" in m.subject}
    above = next(m for s, m in raised.items() if "Candidate B" in s)  # DM-000131, 43.75%
    assert above.to == ["rahul.k@example.com"] and "kavya.r@example.com" in above.cc
    assert "The demand owner decides" in above.text
    below = next(m for s, m in raised.items() if "Candidate A" in s)  # DM-000121, 24.84%
    assert below.to == ["sanjay.m@example.com"] and "leadership decides" in below.text
    assert {"priya.n@example.com", "kavya.r@example.com"} <= set(below.cc)


def test_leaderships_decision_reaches_the_owner_and_the_gtd_team_admin(client: Client, db: Session) -> None:
    import_sample(client)
    mail.sent.clear()
    margin_service.decide(
        db, actor(db, "sanjay"), offer_for(db, "DM-000121").id, "declined", "Margin too thin for this role"
    )
    [m] = mail.sent
    assert set(m.to) == {"priya.n@example.com", "kavya.r@example.com"}
    assert "declined" in m.subject and "Margin too thin for this role" in m.text


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
