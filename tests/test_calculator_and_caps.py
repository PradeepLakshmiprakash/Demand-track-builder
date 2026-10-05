"""Margin calculator (what-if) and the non-billable cap per business unit."""

from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core import mail
from app.models import BusinessUnit
from tests.conftest import Client, bu_id


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def test_calculator_shows_cost_margin_and_who_approves_per_channel(client: Client) -> None:
    c = client.as_user("kavya")
    page = c.get("/margin-calculator?grade=C2&region=US&bill_rate=95").text
    # C2 US: Sogeti cost 68 → 28.4%, below 30%: leadership. Lowest bill for 30% = 68 / 0.7 = 97.14
    assert "Sogeti" in page and "$68.00" in page and "28.4%" in page and "Leadership" in page
    assert "$97.14" in page
    # GTD supply costs 57.80 → 39.2%: the demand owner approves
    assert "39.2%" in page and "Demand owner" in page
    assert "Calculate" in c.get("/margin-calculator").text  # empty form opens fine
    assert "number above 0" in c.get("/margin-calculator?grade=C2&bill_rate=abc").text


def test_calculator_says_when_the_rate_card_has_no_entry(client: Client) -> None:
    page = client.as_user("sanjay").get("/margin-calculator?grade=C2&region=XX&bill_rate=95").text
    assert "No rate card entry for this combination" in page


@pytest.mark.parametrize("who", ["farah", "vikram", "anil"])
def test_calculator_is_not_for_everyone(client: Client, who: str) -> None:
    assert client.as_user(who).get("/margin-calculator").status_code == 403


def test_demand_owners_have_the_calculator_too(client: Client) -> None:
    page = client.as_user("priya").get("/margin-calculator?grade=C2&region=US&bill_rate=110").text
    assert "38.2%" in page and "Demand owner" in page  # Sogeti 68 against 110: hers to approve


def _raise_nb(client: Client) -> str:
    form = {
        "name": "Shadow Java Developer",
        "practice": "Cloud-Java",
        "grade": "C1",
        "category": "Proactive",
        "type": "New",
        "position_type": "Non-billable",
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


def test_gtd_team_admin_sets_caps_and_leadership_sees_them(client: Client, db: Session) -> None:
    pay = bu_id("PAYMENTS")
    assert (
        "err="
        in client.as_user("sanjay").post("/overview/nb-caps", data={f"cap_{pay}": "1"}).headers["location"]
    )
    r = client.as_user("kavya").post("/overview/nb-caps", data={f"cap_{pay}": "1"})
    assert "msg=" in r.headers["location"]
    db.expire_all()
    assert db.get_one(BusinessUnit, pay).nb_cap == 1

    _raise_nb(client)
    assert all("agreed cap" not in m.text for m in mail.sent)  # 1 of 1: within the cap
    mail.sent.clear()
    _raise_nb(client)
    [landed] = mail.sent
    assert "This takes PAYMENTS to 2 against an agreed cap of 1" in landed.text

    page = client.as_user("sanjay").get("/overview").text
    assert "Non-billable positions against the agreed cap" not in page  # only who sets the caps sees them
    page = client.as_user("kavya").get("/overview").text
    assert "Non-billable positions against the agreed cap" in page and "over cap" in page
    assert "2 Cloud-Java" in page and "Save caps" in page
    assert "err=" in client.post("/overview/nb-caps", data={f"cap_{pay}": "many"}).headers["location"]
