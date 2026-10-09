"""Practices and grades as reference tables, day-by-day figures, retention, a re-raised demand's link to
the one it replaces, and the demands list a page at a time."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Account,
    Candidate,
    DailyFigure,
    Demand,
    Grade,
    Interview,
    Practice,
    RateCard,
    StageEvent,
)
from app.services import housekeeping_service
from tests.conftest import Client, user_id


def names(db: Session, model: type, active: bool | None = None) -> set[str]:
    stmt = select(model.name).where(model.account_id == 1)
    if active is not None:
        stmt = stmt.where(model.active == active)
    return set(db.scalars(stmt))


def test_the_lists_are_mirrored_into_tables(db: Session) -> None:
    cfg = db.get_one(Account, 1).settings
    assert names(db, Practice, True) == set(cfg.practices) and names(db, Grade, True) == set(cfg.grades)
    # a demand can't carry a practice the database doesn't know
    with pytest.raises(IntegrityError):
        db.execute(text("UPDATE demands SET practice = 'NOPE-FS' WHERE app_ref = 'DM-000121'"))
    db.rollback()
    # one that arrives through the app (say from a sheet row) is added, as off the list
    d = db.scalars(select(Demand).where(Demand.app_ref == "DM-000121")).one()
    d.practice = "FROM-SHEET"
    db.commit()
    assert "FROM-SHEET" in names(db, Practice, False) and "FROM-SHEET" not in cfg.practices


def test_renaming_a_practice_carries_everywhere(client: Client, db: Session) -> None:
    before = db.scalar(text("SELECT count(*) FROM demands WHERE account_id = 1 AND practice = 'CCA-FS'"))
    rates = db.scalar(text("SELECT count(*) FROM rate_cards WHERE account_id = 1 AND practice = 'CCA-FS'"))
    assert before and rates
    c = client.as_user("anil")
    assert "Rename a practice or a grade" in c.get("/settings").text
    r = c.post("/settings/rename", data={"kind": "practice", "old": "CCA-FS", "new": "CLOUD-FS"})
    assert r.status_code == 303, r.text[-600:]
    assert "CLOUD-FS" in r.headers["location"] and "err=" not in r.headers["location"]
    db.expire_all()
    count = "SELECT count(*) FROM {} WHERE account_id = 1 AND practice = :p"
    assert db.scalar(text(count.format("demands")), {"p": "CLOUD-FS"}) == before
    assert db.scalar(text(count.format("demands")), {"p": "CCA-FS"}) == 0
    assert db.scalar(text(count.format("rate_cards")), {"p": "CLOUD-FS"}) == rates
    cfg = db.get_one(Account, 1).settings
    assert "CLOUD-FS" in cfg.practices and "CCA-FS" not in cfg.practices
    assert "CLOUD-FS" in names(db, Practice, True) and "CCA-FS" not in names(db, Practice)
    # people follow too
    assert db.scalar(text("SELECT count(*) FROM user_practices WHERE practice = 'CCA-FS'")) == 0
    # refused: a name already on the list, and a rename by anyone but the Administrator
    bad = c.post("/settings/rename", data={"kind": "practice", "old": "CLOUD-FS", "new": "DMN-FS"})
    assert "err=" in bad.headers["location"]
    form = {"kind": "grade", "old": "C1", "new": "C1X"}
    assert client.as_user("kavya").post("/settings/rename", data=form).status_code == 403


def test_renaming_a_grade(db: Session) -> None:
    n = housekeeping_service.rename(db, 1, "grade", "C1", "C1-NEW")
    assert n > 0
    assert db.scalar(select(Demand.id).where(Demand.account_id == 1, Demand.grade == "C1")) is None
    assert db.scalar(select(RateCard.id).where(RateCard.account_id == 1, RateCard.grade == "C1-NEW"))
    with pytest.raises(housekeeping_service.HousekeepingError):
        housekeeping_service.rename(db, 1, "grade", "ZZ", "Y")


def test_taking_a_name_off_the_list_keeps_its_demands(client: Client, db: Session) -> None:
    acc = db.get_one(Account, 1)
    cfg = acc.settings
    cfg.practices = [p for p in cfg.practices if p != "TES-FS"]
    acc.settings = cfg
    db.commit()
    assert "TES-FS" in names(db, Practice, False)  # still there for the demands that carry it
    assert db.scalar(select(Demand.id).where(Demand.practice == "TES-FS")) is not None
    assert db.get_one(Account, 1).version >= 2  # every save is counted


def test_the_days_figures_are_stored_once_a_day(client: Client, db: Session) -> None:
    acc = db.get_one(Account, 1)
    today = date.today()
    first = housekeeping_service.capture_figures(db, acc, today)
    db.commit()
    again = housekeeping_service.capture_figures(db, acc, today)
    db.commit()
    assert first.id == again.id and again.open_positions > 0 and again.revenue_lost > 0
    assert len(db.scalars(select(DailyFigure).where(DailyFigure.account_id == 1)).all()) == 1
    page = client.as_user("sanjay").get("/speed").text
    assert "Day by day" in page and f"{again.revenue_lost:,.0f}"[:3] in page


def test_retention_is_off_until_set_then_removes_personal_details(client: Client, db: Session) -> None:
    acc = db.get_one(Account, 1)
    d = db.scalars(select(Demand).where(Demand.app_ref == "DM-000116")).one()  # joined
    c = Candidate(account_id=1, demand_id=d.id, name="Real Person", channel="fte")
    db.add(c)
    db.flush()
    db.add(
        Interview(
            demand_id=d.id, candidate_id=c.id, round="L1", status="completed", comments="Notes",
            feedback_details={"panel_name": "X"}, ratings={"Communication": 7}, outcome="select",
            submitted_at=datetime.now(UTC),
        )
    )  # fmt: skip
    long_ago = datetime.now(UTC) - timedelta(days=400)
    for e in db.scalars(select(StageEvent).where(StageEvent.demand_id == d.id)):
        e.at = long_ago
    db.commit()
    today = date.today()
    assert (
        housekeeping_service.apply_retention(db, acc, today).candidates == 0
    )  # nothing set: nothing removed
    adm = client.as_user("anil")
    assert "Data retention" in adm.get("/settings").text
    r = adm.post("/settings/retention", data={"candidate_days": "365", "sheet_imports_kept": ""})
    assert "msg=Saved" in r.headers["location"]
    assert "err=" in adm.post("/settings/retention", data={"candidate_days": "5"}).headers["location"]
    db.expire_all()
    acc = db.get_one(Account, 1)
    out = housekeeping_service.apply_retention(db, acc, today)
    db.commit()
    assert out.candidates >= 1
    db.expire_all()
    kept = db.get_one(Candidate, c.id)
    iv = db.scalars(select(Interview).where(Interview.candidate_id == c.id)).one()
    assert kept.name == housekeeping_service.REMOVED
    assert iv.comments is None and iv.feedback_details == {} and iv.outcome == "select"  # the decision stays
    # candidates on demands that haven't been finished that long keep their names
    others = set(db.scalars(select(Candidate.name).where(Candidate.id != c.id)))
    assert others and housekeeping_service.REMOVED not in others


def test_a_re_raised_demand_points_at_the_one_it_replaces(client: Client, db: Session) -> None:
    old = db.scalars(select(Demand).where(Demand.app_ref == "DM-000110")).one()  # cancelled, BANKING
    new = db.scalars(select(Demand).where(Demand.app_ref == "DM-000126")).one()  # Meera's, BANKING
    assert old.bu_id == new.bu_id
    c = client.as_user("meera")
    assert "re-raises an abandoned demand" in c.get("/demands/DM-000126").text
    r = c.post("/demands/DM-000126/replaces", data={"replaces": "DM-000110"})
    assert "Linked" in r.headers["location"]
    page = c.get("/demands/DM-000126").text
    assert "Re-raises" in page and "/demands/DM-000110" in page
    assert "Re-raised as" in client.as_user("kavya").get("/demands/DM-000110").text
    # not a demand from another business unit, and not by someone who doesn't own it
    assert "err=" in c.post("/demands/DM-000126/replaces", data={"replaces": "DM-000121"}).headers["location"]
    other = client.as_user("farah").post("/demands/DM-000126/replaces", data={"replaces": ""})
    assert "err=" in other.headers["location"]
    c = client.as_user("meera")
    assert "removed" in c.post("/demands/DM-000126/replaces", data={"replaces": ""}).headers["location"]


def test_the_demands_list_comes_a_page_at_a_time(client: Client, db: Session) -> None:
    owner, bu = user_id("priya"), db.scalar(select(Demand.bu_id).where(Demand.app_ref == "DM-000121"))
    start = date.today() + timedelta(days=30)
    for n in range(60):
        db.add(
            Demand(
                account_id=1, bu_id=bu, owner_id=owner, name=f"Bulk position {n:02d}", practice="CCA-FS",
                grade="C1", status="submitted", start_date=start,
            )
        )  # fmt: skip
    db.commit()
    c = client.as_user("kavya")
    one = c.get("/demands").text
    assert "Page 1 of 2" in one and one.count('class="tr link"') == 50 and "page=2" in one
    two = c.get("/demands?page=2").text
    assert "Page 2 of 2" in two and 0 < two.count('class="tr link"') < 50 and "Previous" in two
    assert "Page 2 of 2" in c.get("/demands?page=99").text  # past the end: the last page
