"""What the demand owner gained (1 Oct): revenue loss on their own demands, and raising the offer
approval themselves once the panel has selected a candidate."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.models import Account, Demand, OfferApproval
from app.services import interview_service as svc
from app.services.interview_service import Feedback
from tests.conftest import RATINGS, Client, user_id


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def demand(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def _select(db: Session, ref: str, name: str) -> int:
    """The panel selects a candidate on the demand; returns the candidate's id."""
    d = demand(db, ref)
    c = svc.ensure_candidate(db, 1, d, name)
    ratings = dict(RATINGS)
    fb = Feedback(round="L1", ratings=ratings, outcome="select", comments="good")
    svc.record_feedback(db, db.get_one(Account, 1), user_id("vikram"), c, fb)
    return c.id


def test_owner_sees_revenue_lost_on_their_own_demands(client: Client) -> None:
    c = client.as_user("priya")
    listing = c.get("/demands").text
    assert "Revenue lost to date" in listing and "of my demands unfilled past their start" in listing
    page = c.get("/demands/DM-000117").text  # past its start, no joining date
    assert "Revenue lost to date" in page and "working days unfilled since" in page
    assert "Client bill rate" in page  # the owner sees the rate on their own demand
    # not for someone else's demand, and not for the GTD admin team
    assert "Revenue lost to date" not in client.as_user("farah").get("/demands/DM-000117").text


def test_owner_asks_for_the_offer_approval(client: Client, db: Session) -> None:
    cid = _select(db, "DM-000146", "Asked For")  # Neha's demand; client interview required
    assert demand(db, "DM-000146").status == "panel_selected"
    mail.sent.clear()
    neha = client.as_user("neha")
    page = neha.get("/demands/DM-000146").text
    assert "Ask for offer approval" in page and "selected by the panel, no approval yet" in page

    r = neha.post("/demands/DM-000146/offers", data={"candidate_id": str(cid), "channel": "sogeti"})
    assert r.status_code == 303 and "err=" not in r.headers["location"]
    offer = db.scalars(select(OfferApproval).where(OfferApproval.candidate_id == cid)).one()
    # D1 Sogeti: cost 80, bill 125 → 36%: the lead admin decides
    assert offer.channel == "sogeti" and offer.route == "admin"
    [m] = mail.sent  # at 36% it is hers to decide; the lead admin is notified
    assert m.to == ["neha.t@example.com"] and m.cc == ["kavya.r@example.com"]
    assert "Offer approval needed for Asked For" in m.subject and "Steps:" in m.text

    page = neha.get("/demands/DM-000146").text
    assert "Waiting for approval" in page and "Ask for offer approval" not in page
    assert "margin 36.0%" in page and ">Decide</a>" in page
    r = neha.post(f"/approvals/{offer.id}/decide", data={"decision": "approved", "comment": ""})
    assert "msg=Offer" in r.headers["location"]
    assert "Approved" in neha.get("/demands/DM-000146").text
    assert (
        "err="
        in neha.post(
            "/demands/DM-000146/offers", data={"candidate_id": str(cid), "channel": "sogeti"}
        ).headers["location"]
    )  # once per candidate


def test_only_the_owner_asks_and_only_for_a_selected_candidate(client: Client, db: Session) -> None:
    cid = _select(db, "DM-000146", "Asked For")
    form = {"candidate_id": str(cid), "channel": "sogeti"}
    for who in ("kavya", "rahul"):  # the lead admin, and a BU colleague who can only read it
        r = client.as_user(who).post("/demands/DM-000146/offers", data=form)
        assert "err=" in r.headers["location"]
    assert client.as_user("priya").post("/demands/DM-000146/offers", data=form).status_code == 404
    neha = client.as_user("neha")
    assert (
        "err="
        in neha.post("/demands/DM-000146/offers", data={**form, "candidate_id": "999"}).headers["location"]
    )
    assert db.scalar(select(OfferApproval).where(OfferApproval.candidate_id == cid)) is None


def test_owner_records_the_joining_date_after_approval(client: Client, db: Session) -> None:
    from datetime import date, timedelta

    from seed import sample_sheet

    cid = _select(db, "DM-000146", "Asked For")
    neha = client.as_user("neha")
    when = (date.today() + timedelta(days=50)).isoformat()
    # not before an offer is approved
    assert (
        "err="
        in neha.post("/demands/DM-000146/joining-date", data={"expected_doj": when}).headers["location"]
    )
    assert "Record joining date" not in neha.get("/demands/DM-000146").text

    neha.post("/demands/DM-000146/offers", data={"candidate_id": str(cid), "channel": "sogeti"})
    offer = db.scalars(select(OfferApproval).where(OfferApproval.candidate_id == cid)).one()
    neha.post(f"/approvals/{offer.id}/decide", data={"decision": "approved", "comment": ""})
    page = neha.get("/demands/DM-000146").text
    assert "Offer accepted?" in page and "Not known yet" in page
    for who in ("kavya", "farah"):  # only the owner records it
        r = client.as_user(who).post("/demands/DM-000146/joining-date", data={"expected_doj": when})
        assert "err=" in r.headers["location"]

    mail.sent.clear()
    r = client.as_user("neha").post("/demands/DM-000146/joining-date", data={"expected_doj": when})
    assert "msg=" in r.headers["location"]
    d = demand(db, "DM-000146")
    assert d.expected_doj is not None and d.status == "offer_in_market"  # offer made, joining awaited
    [m] = mail.sent
    assert m.to == ["kavya.r@example.com"] and "Offer accepted, joining" in m.subject
    page = client.get("/demands/DM-000146").text
    assert "expected, entered by the demand owner" in page and "Client Onboarding In Progress" in page
    row = next(x for x in client.get("/api/demands").json() if x["app_ref"] == "DM-000146")
    assert row["joining_date"] == when and row["main_stage"] == "Client Onboarding In Progress"

    # The next BCM sheet still says "Coverage Required" for it: the owner's progress isn't undone.
    files = {"file": ("dp.xlsx", sample_sheet.build(), "application/octet-stream")}
    client.as_user("farah").post("/imports", data={"sheet_date": date.today().isoformat()}, files=files)
    assert demand(db, "DM-000146").status == "offer_in_market"
