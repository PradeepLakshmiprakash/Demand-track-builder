"""Requests to the Administrator: the GTD team admin asks by email, the Administrator does it."""

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
    assert client.as_user("farah").get("/requests").status_code == 403
    assert client.as_user("priya").post("/requests", data=form).status_code == 403
    client.as_user("kavya").post("/requests", data=form)
    assert client.post("/requests/1", data={"status": "done"}).status_code == 403  # Kavya can't mark her own
    # another account's Administrator can't see or handle it
    assert "grace period" not in client.as_user("rosa").get("/requests").text
    assert "err=" in client.post("/requests/1", data={"status": "done"}).headers["location"]
