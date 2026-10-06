"""Requests to the Administrator: the lead admin asks by email, the Administrator does it."""

import pytest

from app.core import mail
from tests.conftest import Client


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def test_gtd_team_admin_asks_and_the_administrator_is_mailed(client: Client) -> None:
    r = client.as_user("kavya").post(
        "/requests", data={"kind": "rate_card", "details": "Sogeti C2 US goes to 70/h from 1 Nov 2026"}
    )
    assert r.status_code == 303 and "sent" in r.headers["location"]
    [m] = mail.sent
    assert m.to == ["anil.v@example.com"] and m.cc == ["kavya.r@example.com"]
    assert "Request #1: Rate card" in m.subject and "Sogeti C2 US goes to 70/h" in m.text
    assert "Steps:" in m.text
    assert "Sogeti C2 US" in client.as_user("anil").get("/requests").text


def test_administrator_marks_it_done_and_the_requester_is_told(client: Client) -> None:
    client.as_user("kavya").post(
        "/requests", data={"kind": "access", "details": "Add Asha P. as owner in CARDS"}
    )
    mail.sent.clear()
    r = client.as_user("anil").post("/requests/1", data={"status": "done", "note": "Added today"})
    assert r.status_code == 303
    [m] = mail.sent
    assert m.to == ["kavya.r@example.com"] and "Request #1 done" in m.subject and "Added today" in m.text
    assert "Done by Anil V." in client.as_user("kavya").get("/requests").text
    # handled once only
    assert "err=" in client.as_user("anil").post("/requests/1", data={"status": "done"}).headers["location"]


def test_decline_needs_a_reason(client: Client) -> None:
    client.as_user("kavya").post("/requests", data={"kind": "other", "details": "Please rename the account"})
    r = client.as_user("anil").post("/requests/1", data={"status": "declined", "note": ""})
    assert "err=" in r.headers["location"]


def test_only_the_right_roles(client: Client) -> None:
    form = {"kind": "settings", "details": "Change the grace period to 5 days"}
    assert client.as_user("anil").post("/requests", data=form).status_code == 403  # raises nothing himself
    client.as_user("kavya").post("/requests", data=form)
    assert client.post("/requests/1", data={"status": "done"}).status_code == 403  # Kavya can't mark her own
    # another account's Administrator can't see or handle it
    assert "grace period" not in client.as_user("rosa").get("/requests").text
    assert "err=" in client.post("/requests/1", data={"status": "done"}).headers["location"]


@pytest.mark.parametrize(
    ("who", "kind", "details"),
    [
        ("priya", "data_fix", "The client bill rate on DM-000121 should be 98, not 95"),
        ("farah", "settings", "Map the new BCM status On Hold - Client to a stage"),
        ("sanjay", "escalation", "Inform me of overdue escalations for high severity only"),
        ("vikram", "profile", "Add Kafka to my interview skills"),
    ],
)
def test_every_role_can_ask_for_what_it_needs(client: Client, who: str, kind: str, details: str) -> None:
    c = client.as_user(who)
    page = c.get("/requests").text
    assert "New request" in page and f'value="{kind}"' in page
    r = c.post("/requests", data={"kind": kind, "details": details})
    assert r.status_code == 303 and "sent" in r.headers["location"]
    [m] = mail.sent
    assert m.to == ["anil.v@example.com"] and details in m.text
    assert details in c.get("/requests").text  # their own request
    assert details in client.as_user("anil").get("/requests").text  # the Administrator sees it
    assert details in client.as_user("kavya").get("/requests").text  # so does the Lead admin


def test_people_see_only_their_own_requests_and_their_own_kinds(client: Client) -> None:
    client.as_user("priya").post(
        "/requests", data={"kind": "access", "details": "Move the PAYMENTS demands to me"}
    )
    page = client.as_user("neha").get("/requests").text  # another demand owner
    assert "Move the PAYMENTS demands" not in page and "You have not asked" in page
    # an interviewer isn't offered the rate card, and can't post it either
    assert 'value="rate_card"' not in client.as_user("vikram").get("/requests").text
    r = client.as_user("vikram").post("/requests", data={"kind": "rate_card", "details": "Change a rate"})
    assert "err=" in r.headers["location"]


def test_lead_admin_special_access_is_on_record(client: Client) -> None:
    ask = "Lead admin access to see client bill rates and margins on every demand"
    client.as_user("kavya").post("/requests", data={"kind": "special", "details": ask})
    assert "Special access you hold" not in client.get("/requests").text  # asked, not yet given
    client.as_user("anil").post("/requests/1", data={"status": "done", "note": "Granted"})
    page = client.as_user("kavya").get("/requests").text
    assert "Special access you hold" in page and "In place" in page and "Granted by Anil V." in page
    assert "Lead admin" in page  # how the role reads when Kavya is signed in
