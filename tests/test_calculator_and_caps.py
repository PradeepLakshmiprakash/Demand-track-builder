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


def test_calculator_says_what_a_client_rate_affords(client: Client) -> None:
    c = client.as_user("kavya")
    page = c.get("/margin-calculator?bill_rate=100&practice=CCA-FS").text
    # At $100 and 30%, the most a position can cost is $70: in CCA-FS that is C2 ($68, 32%).
    assert "put forward <strong>C2 in CCA-FS</strong>" in page and "$68.00" in page and "32.0%" in page
    assert "$70.00" in page and "Grades around this rate in CCA-FS" in page
    assert "D1" in page and "Leadership" in page  # the next grade up costs $80: under the margin
    best = c.get("/margin-calculator?bill_rate=100").text  # no practice: the best of each
    assert "The best each practice can do" in best and "DCX-FS" in best and "Supply channel" not in best
    assert "Work it out" in c.get("/margin-calculator").text  # opens on $100 with the account's margin
    assert "number above 0" in c.get("/margin-calculator?bill_rate=abc").text
    tried = c.get("/margin-calculator?bill_rate=100&practice=CCA-FS&hold=40").text
    assert "Trying 40%, not the 30% margin" in tried and "put forward <strong>C1 in CCA-FS</strong>" in tried


def test_calculator_says_when_nothing_fits(client: Client) -> None:
    page = client.as_user("sanjay").get("/margin-calculator?bill_rate=30&practice=CCA-FS").text
    assert "Nothing in <strong>CCA-FS</strong> clears 30%" in page


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
