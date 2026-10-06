"""Margin calculator (what-if) and the non-billable cap per business unit."""

from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.core import mail
from app.models import Account, BusinessUnit
from tests.conftest import Client, bu_id


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def test_calculator_gives_the_margin_for_a_practice_and_grade(client: Client) -> None:
    c = client.as_user("kavya")
    page = c.get("/margin-calculator?bill_rate=100&practice=CCA-FS&grade=C2").text
    # C2 in CCA-FS costs $68: at $100 that leaves 32%, above the 30% margin.
    assert "<strong>C2 in CCA-FS</strong>" in page and "$68.00" in page
    assert "32.0%" in page and "$32.00" in page and "the demand owner approves" in page
    assert "Margin to hold" not in page and "Supply channel" not in page
    assert page.index("Recommendations") < page.index(
        "<strong>C2 in CCA-FS</strong>"
    )  # the wording comes last
    low = c.get("/margin-calculator?bill_rate=100&practice=CCA-FS&grade=D1").text  # $80: 20%
    assert "20.0%" in low and "leadership decides" in low and "at most $70.00" in low
    assert "Calculate margin" in c.get("/margin-calculator").text  # opens with nothing worked out
    assert "number above 0" in c.get("/margin-calculator?bill_rate=abc&practice=CCA-FS&grade=C2").text


def test_calculator_recommends_a_few_alternatives(db: Session) -> None:
    from datetime import date as day
    from decimal import Decimal

    from app.services import rate_card_service as rc

    rows = rc.offerings(db, 1, Decimal("100"), Decimal("30"), day(2026, 9, 1))
    cfg = db.get_one(Account, 1).settings
    out = rc.suggestions(rows, "Cloud-MF", "C1", Decimal("30"), cfg.shared_stacks)
    assert (
        len(out) <= 5
        and out[0].chosen
        and (out[0].offering.practice, out[0].offering.grade) == ("Cloud-MF", "C1")
    )
    by_why = {w: s for s in out for w in s.why}
    near, top = by_why["Closest to the 30% margin"], by_why["Highest margin"]
    # In Cloud-MF at $100: C2 costs $63.92 (36.1%), the last grade that clears 30%; A5 earns the most.
    same = [s for s in out if s.offering.practice == "Cloud-MF" and not s.chosen]
    assert {(s.offering.grade, tuple(s.why)) for s in same} == {
        ("C2", ("Closest to the 30% margin",)),
        ("A5", ("Highest margin",)),
    }
    # Other practices are only those that take the same skills (cloud), at the same grade.
    other = [s for s in out if s.offering.practice != "Cloud-MF"]
    assert other and all(s.offering.grade == "C1" and "Cloud and DevOps" in s.where for s in other)
    assert {s.offering.practice for s in other} <= {"CCA-FS", "ADM-FS", "Cloud-Java", "Cloud-APM"}
    assert near.offering.fits and top.offering.margin_pct >= near.offering.margin_pct
    assert cfg.shared_stacks("TES-FS", "DCX-FS") == []
    assert cfg.shared_stacks("tes-fs", "CCA-FS") == ["Testing and QA"]


def test_administrator_sets_each_practice_tech_stack(client: Client, db: Session) -> None:
    anil = client.as_user("anil")
    page = anil.get("/settings").text
    assert "Practices and their tech stack" in page
    assert 'value="Cloud and DevOps"' in page  # the starting guess
    cfg = db.get_one(Account, 1).settings
    form = {"practice": list(cfg.practices), "stacks": ["" for _ in cfg.practices]}
    form["stacks"][cfg.practices.index("Cloud-MF")] = "Mainframe,  COBOL , mainframe"
    form["stacks"][cfg.practices.index("TES-FS")] = "Selenium, cobol"
    r = anil.post("/settings/practice-stacks", data=form)
    assert r.status_code == 303 and "msg=Saved" in r.headers["location"]
    db.expire_all()
    cfg = db.get_one(Account, 1).settings
    assert cfg.stacks_of("Cloud-MF") == ["Mainframe", "COBOL"]  # tidied, no repeats
    assert cfg.shared_stacks("Cloud-MF", "TES-FS") == ["COBOL"]  # matched whatever the case
    assert cfg.stacks_of("CCA-FS") == [] and cfg.shared_stacks("Cloud-MF", "Cloud-Java") == []
    # The calculator now compares Cloud-MF with TES-FS, and no longer with the cloud practices.
    page = client.as_user("kavya").get("/margin-calculator?bill_rate=100&practice=Cloud-MF&grade=C1").text
    assert "Also takes COBOL" in page and "Cloud and DevOps" not in page
    assert client.as_user("kavya").post("/settings/practice-stacks", data=form).status_code == 403


def test_calculator_says_when_the_card_has_no_cost(client: Client, db: Session) -> None:
    from sqlalchemy import delete

    from app.models import RateCard

    db.execute(delete(RateCard).where(RateCard.practice == "TES-FS", RateCard.grade == "E1"))
    db.commit()
    page = client.as_user("sanjay").get("/margin-calculator?bill_rate=90&practice=TES-FS&grade=E1").text
    assert "The rate card has no cost for <strong>E1 in TES-FS</strong>" in page


@pytest.mark.parametrize("who", ["farah", "vikram", "anil"])
def test_calculator_is_not_for_everyone(client: Client, who: str) -> None:
    assert client.as_user(who).get("/margin-calculator").status_code == 403


def test_demand_owners_have_the_calculator_too(client: Client) -> None:
    page = client.as_user("priya").get("/margin-calculator?practice=CCA-FS&grade=C2&bill_rate=110").text
    assert "38.2%" in page and "Demand owner" in page  # $68 against $110: hers to approve


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


def test_calculator_has_three_pages(client: Client) -> None:
    c = client.as_user("kavya")
    for path in ("/margin-calculator", "/margin-calculator/team", "/margin-calculator/pod"):
        page = c.get(path).text
        for name in ("Individual contribution margin", "Team contribution margin", "Pod contribution margin"):
            assert name in page
    assert client.as_user("farah").get("/margin-calculator/team").status_code == 403


def test_team_contribution_margin(client: Client, db: Session) -> None:
    from datetime import date as day
    from decimal import Decimal

    from app.services import rate_card_service as rc
    from app.services.rate_card_service import Member

    acc = db.get_one(Account, 1)
    team = [Member("CCA-FS", "C2", 2, rate=Decimal("100")), Member("TES-FS", "B1", 1, rate=Decimal("50"))]
    r = rc.team_contribution(db, acc, team, day(2026, 9, 1))
    # 2 × $100 + 1 × $50 = $250 billed; 2 × $68 + 1 × $40.50 = $176.50 cost → 29.4%.
    assert (r.revenue, r.cost, r.margin_pct) == (Decimal("250"), Decimal("176.50"), Decimal("29.40"))
    assert not r.fits and r.target_revenue == Decimal("252.14") and r.headcount == 3
    assert team[0].margin_pct == Decimal("32.00") and team[1].margin_pct == Decimal("19.00")
    url = "/margin-calculator/team?practice=CCA-FS&grade=C2&count=2&rate=100"
    url += "&practice=TES-FS&grade=B1&count=1&rate=50"
    page = client.as_user("kavya").get(url).text
    assert "29.4%" in page and "below the account" in page and "$252.14" in page and "Role by role" in page
    bad = client.as_user("kavya").get("/margin-calculator/team?practice=CCA-FS&grade=C2&count=2&rate=").text
    assert "enter the client rate per hour for C2 in CCA-FS" in bad


def test_pod_contribution_margin(client: Client, db: Session) -> None:
    from datetime import date as day
    from decimal import Decimal

    from app.services import rate_card_service as rc
    from app.services.rate_card_service import Member

    acc = db.get_one(Account, 1)
    pod = [Member("CCA-FS", "C2", 2), Member("TES-FS", "B1", 1, allocation=Decimal("50"))]
    r = rc.pod_contribution(db, acc, pod, Decimal("40000"), day(2026, 9, 1))
    # 2 × $68 + 0.5 × $40.50 = $156.25 an hour; × 168 hours = $26,250 a month against $40,000 → 34.4%.
    assert r.cost == Decimal("156.25") and r.monthly(r.cost) == Decimal("26250.00")
    assert r.margin_pct == Decimal("34.38") and r.fits and r.headcount == Decimal("2.5")
    url = "/margin-calculator/pod?price=40000&practice=CCA-FS&grade=C2&count=2&allocation=100"
    url += "&practice=TES-FS&grade=B1&count=1&allocation=50"
    page = client.as_user("sanjay").get(url).text
    assert "34.4%" in page and "$26,250" in page and "Who the pod is made of" in page
    assert "Pod price, per month" in client.as_user("sanjay").get("/margin-calculator/pod").text
