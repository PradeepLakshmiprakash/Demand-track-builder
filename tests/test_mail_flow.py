"""Mail flow for a new demand: an email the moment it lands, a reminder every morning until it is
created on GTD, and a completion email."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.models import Demand
from app.services import notify_service
from tests.conftest import Client

TEAM = ["kavya.r@example.com", "deepak.l@example.com", "farah.q@example.com"]


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def _raise(client: Client) -> str:
    form = {
        "name": "Java Developer",
        "practice": "Cloud-Java",
        "grade": "C1",
        "category": "Open",
        "type": "New",
        "position_type": "Billable",
        "primary_skills": "Java",
        "region": "US",
        "location": "Chicago",
        "start_date": (date.today() + timedelta(days=30)).isoformat(),
        "work_mode": "Hybrid",
        "action": "submit",
    }
    r = client.as_user("priya").post("/demands/new", data=form)
    assert r.status_code == 303, r.text
    return r.headers["location"].split("/")[2].split("?")[0]


def test_email_goes_out_the_moment_a_demand_lands(client: Client) -> None:
    ref = _raise(client)
    [m] = mail.sent
    assert sorted(m.to) == sorted(TEAM) and m.cc == ["priya.n@example.com"]
    assert f"New demand to create on GTD: {ref} Java Developer" in m.subject
    assert "Steps for the GTD admin team" in m.text and "reminded every morning" in m.text


def test_a_draft_sends_nothing(client: Client) -> None:
    form = {"name": "Draft only", "action": "draft"}
    client.as_user("priya").post("/demands/new", data=form)
    assert mail.sent == []


def test_morning_reminder_until_created_then_a_completion_email(client: Client, db: Session) -> None:
    ref = _raise(client)
    d = db.scalars(select(Demand).where(Demand.app_ref == ref)).one()
    mail.sent.clear()

    notify_service.send_daily_admin_mail(db, 1, force=True)  # first morning
    notify_service.send_daily_admin_mail(db, 1, force=True)  # next morning: still not created
    assert len(mail.sent) == 2
    for m in mail.sent:
        assert ref in m.text and "priya.n@example.com" in m.cc  # the owner is copied on the reminder
    assert "Still without a requisition ID" in mail.sent[1].text

    listing = client.as_user("farah").get("/demands").text
    assert "GTD created: <strong>No</strong>" in listing
    mail.sent.clear()
    client.post(f"/gtd-queue/{d.id}/link", data={"gtd_req_id": "NEW123"})
    [done] = mail.sent
    assert done.to == ["priya.n@example.com"] and sorted(done.cc) == sorted(TEAM)
    assert f"Created on GTD: {ref} is NEW123" in done.subject and "Farah Q." in done.text

    mail.sent.clear()
    notify_service.send_daily_admin_mail(db, 1, force=True)  # no more reminders for it
    assert all(ref not in m.text for m in mail.sent)
