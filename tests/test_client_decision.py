"""The client has no access: the demand owner records the client interview result (or the BCM sheet does)."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.models import Account, Candidate, Demand, OfferApproval
from app.services import interview_service as svc
from tests.conftest import Client, user_id
from tests.test_progress import by_ref, fb, import_sample


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def panel_selects(db: Session, name: str) -> Candidate:
    d = by_ref(db, "DM-000146")  # Neha's; client interview required
    c = svc.ensure_candidate(db, 1, d, name)
    svc.record_feedback(db, db.get_one(Account, 1), user_id("vikram"), c, fb())
    assert by_ref(db, "DM-000146").status == "panel_selected"
    return c


def decide(client: Client, who: str, cand: Candidate, outcome: str, **kw: str) -> str:
    r = client.as_user(who).post(
        "/demands/DM-000146/client-decision",
        data={"candidate_id": str(cand.id), "outcome": outcome, **kw},
    )
    assert r.status_code == 303
    return r.headers["location"]


def test_owner_records_that_the_client_selected(client: Client, db: Session) -> None:
    c = panel_selects(db, "Chosen One")
    page = client.as_user("neha").get("/demands/DM-000146").text
    assert "Client selected" in page and "Client did not select" in page
    assert "Client selected" not in client.as_user("farah").get("/demands/DM-000146").text  # owner only

    assert "err=" in decide(client, "neha", c, "select")  # the channel prices the offer
    mail.sent.clear()
    assert "msg=" in decide(client, "neha", c, "select", channel="sogeti")
    d = by_ref(db, "DM-000146")
    assert d.status == "offer_in_process" and d.status_enum.main.label == "Client Onboarding In Progress"
    offer = db.scalars(select(OfferApproval).where(OfferApproval.demand_id == d.id)).one()
    assert offer.candidate_id == c.id and offer.channel == "sogeti"
    assert any("Client interview: Chosen One selected" in m.subject for m in mail.sent)
    page = client.as_user("neha").get("/demands/DM-000146").text
    assert "Client · selected" in page and "Client did not select" not in page

    import_sample(client, date.today())  # the sheet still says Coverage Required: it doesn't undo this
    assert by_ref(db, "DM-000146").status == "offer_in_process"


def test_owner_records_that_the_client_did_not_select(client: Client, db: Session) -> None:
    c = panel_selects(db, "Not This Time")
    assert "err=" in decide(client, "neha", c, "reject")  # a reason is needed
    assert "msg=" in decide(client, "neha", c, "reject", note="Wanted more payments experience")
    d = by_ref(db, "DM-000146")
    assert d.status == "coverage_required"  # nobody else in play: back to sourcing
    db.refresh(c)
    assert c.client_outcome == "reject" and c.client_decided_by == user_id("neha")
    assert not db.scalars(select(OfferApproval).where(OfferApproval.demand_id == d.id)).all()
    [m] = [m for m in mail.sent if "not selected" in m.subject]
    assert "Wanted more payments experience" in m.text and "neha.t@example.com" in m.cc
    page = client.as_user("neha").get("/demands/DM-000146").text
    assert "Client · not selected" in page and "Ask for offer approval" not in page


def test_another_candidate_keeps_the_demand_with_the_client(client: Client, db: Session) -> None:
    a = panel_selects(db, "First Person")
    b = panel_selects(db, "Second Person")
    decide(client, "neha", a, "reject", note="No")
    assert by_ref(db, "DM-000146").status == "panel_selected"  # Second Person is still with the client
    decide(client, "neha", b, "select", channel="sogeti")
    assert by_ref(db, "DM-000146").status == "offer_in_process"


def test_only_the_owner_records_it(client: Client, db: Session) -> None:
    c = panel_selects(db, "Someone")
    assert "err=" in decide(client, "kavya", c, "select", channel="sogeti")
    assert db.scalars(select(Demand).where(Demand.app_ref == "DM-000146")).one().status == "panel_selected"


def test_owner_marks_the_client_interview_started(client: Client, db: Session) -> None:
    c = panel_selects(db, "With Client")
    assert "Client interview started" in client.as_user("neha").get("/demands/DM-000146").text
    r = client.as_user("kavya").post("/demands/DM-000146/client-interview")
    assert "err=" in r.headers["location"]  # the owner's to record
    mail.sent.clear()
    r = client.as_user("neha").post("/demands/DM-000146/client-interview")
    assert "msg=" in r.headers["location"]
    d = by_ref(db, "DM-000146")
    assert (
        d.status == "profiles_with_client"
        and d.status_enum.full == "Selection In Progress · Client interview in progress"
    )
    assert any("Client interview started" in m.subject for m in mail.sent)
    page = client.as_user("neha").get("/demands/DM-000146").text
    assert "Has the client started interviewing?" not in page and "Client did not select" in page

    import_sample(client, date.today())  # a sheet still on Coverage Required doesn't undo it
    assert by_ref(db, "DM-000146").status == "profiles_with_client"
    decide(client, "neha", c, "reject", note="Not a fit")
    assert by_ref(db, "DM-000146").status == "coverage_required"
