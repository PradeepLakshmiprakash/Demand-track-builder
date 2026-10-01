"""Flow 1: demand intake. Draft, save, submit, N positions, validation, edit rights, JD."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Demand, StageEvent
from tests.conftest import Client, bu_id

START = (date.today() + timedelta(days=40)).isoformat()


def form(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Java AWS Developer Payments Chicago", "practice": "CCA-FS", "grade": "C1",
        "category": "Open", "type": "New", "position_type": "Billable", "primary_skills": "Java, AWS",
        "secondary_skills": "", "exp_min": "6", "exp_max": "10", "client_rate": "", "start_date": START,
        "region": "US", "location": "Chicago", "work_mode": "Hybrid", "hiring_manager": "", "positions": "1",
        "action": "submit",
    }  # fmt: skip
    base.update(kw)
    return base


def demand(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def ref_from(r: object) -> str:
    loc = r.headers["location"]  # type: ignore[attr-defined]
    assert loc.startswith("/demands/DM-"), loc
    return loc.split("/")[2].split("?")[0]


@pytest.fixture
def priya(client: Client) -> Client:
    return client.as_user("priya")


def test_save_draft_gets_app_ref_from_postgres(priya: Client, db: Session) -> None:
    r = priya.post("/demands/new", data=form(action="draft", start_date="", practice=""))
    ref = ref_from(r)
    assert ref == "DM-000152"
    d = demand(db, ref)
    assert d.status == "draft" and d.submitted_at is None
    assert d.gtd_name == "Java AWS Developer Payments Chicago"


def test_submit_puts_it_in_the_queue(priya: Client, db: Session) -> None:
    ref = ref_from(priya.post("/demands/new", data=form()))
    d = demand(db, ref)
    assert d.status == "submitted" and d.submitted_at is not None
    assert d.primary_skills == ["Java", "AWS"] and (d.exp_min, d.exp_max) == (6, 10)
    stages = [
        e.to_stage
        for e in db.scalars(select(StageEvent).where(StageEvent.demand_id == d.id).order_by(StageEvent.id))
    ]
    assert stages == ["draft", "submitted"]


def test_demand_owner_always_raises_for_own_bu(priya: Client, db: Session) -> None:
    ref = ref_from(priya.post("/demands/new", data=form(bu_id=str(bu_id("CARDS")))))
    assert demand(db, ref).business_unit.name == "PAYMENTS"


def test_admin_picks_the_bu(client: Client, db: Session) -> None:
    client.as_user("kavya")
    assert client.post("/demands/new", data=form()).status_code == 400  # no BU chosen
    ref = ref_from(client.post("/demands/new", data=form(bu_id=str(bu_id("DATA")))))
    assert demand(db, ref).business_unit.name == "DATA"


def test_n_positions_make_n_demands(priya: Client, db: Session) -> None:
    r = priya.post("/demands/new", data=form(positions="3"))
    assert "3+demands" in r.headers["location"] or "3%20demands" in r.headers["location"]
    refs = db.scalars(
        select(Demand.app_ref).where(Demand.app_ref > "DM-000151").order_by(Demand.app_ref)
    ).all()
    assert refs == ["DM-000152", "DM-000153", "DM-000154"]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"practice": ""}, "Needed before submitting: Practice"),
        ({"start_date": (date.today() - timedelta(days=1)).isoformat()}, "start date is in the past"),
        ({"type": "Replacement"}, "Who is being replaced"),
        ({"practice": "XYZ-FS"}, "isn&#39;t in the account&#39;s list"),
        ({"exp_min": "12", "exp_max": "4"}, "Experience min is more than max"),
        ({"name": ""}, "Name"),
        ({"positions": "50"}, "Positions"),
    ],
)
def test_submit_validation(priya: Client, db: Session, change: dict[str, str], message: str) -> None:
    r = priya.post("/demands/new", data=form(**change))
    assert r.status_code == 400
    assert message in r.text
    assert db.scalar(select(Demand).where(Demand.app_ref == "DM-000152")) is None


def test_owner_sees_their_own_bill_rate(priya: Client, client: Client, db: Session) -> None:
    ref = ref_from(priya.post("/demands/new", data=form(client_rate="98.50", action="draft")))
    assert "$98.50" in priya.get(f"/demands/{ref}").text
    assert "98.50" in priya.get(f"/demands/{ref}/edit").text
    priya.post(f"/demands/{ref}/edit", data=form(client_rate="", action="submit", name="Renamed"))
    d = demand(db, ref)
    assert d.name == "Renamed" and str(d.client_rate) == "98.50"  # blank kept it
    assert "$98.50" in client.as_user("kavya").get(f"/demands/{ref}").text
    assert "98.50" not in client.as_user("farah").get(f"/demands/{ref}").text  # not the GTD admin team


def test_edit_until_it_goes_out_in_the_mail(priya: Client, db: Session) -> None:
    ref = ref_from(priya.post("/demands/new", data=form()))
    assert priya.post(f"/demands/{ref}/edit", data=form(name="Still editable")).status_code == 303
    d = demand(db, ref)
    d.status = "notified"
    db.commit()
    assert priya.get(f"/demands/{ref}/edit").status_code == 403
    assert priya.post(f"/demands/{ref}/edit", data=form()).status_code == 403


def test_submit_draft_from_detail_page(priya: Client, db: Session) -> None:
    ref = ref_from(priya.post("/demands/new", data=form(action="draft")))
    r = priya.post(f"/demands/{ref}/submit")
    assert "msg=Submitted" in r.headers["location"]
    assert demand(db, ref).status == "submitted"


def test_incomplete_draft_submit_goes_back_to_form(priya: Client, db: Session) -> None:
    ref = ref_from(priya.post("/demands/new", data=form(action="draft", practice="")))
    r = priya.post(f"/demands/{ref}/submit")
    assert r.headers["location"].startswith(f"/demands/{ref}/edit?err=")
    assert demand(db, ref).status == "draft"


def test_others_cannot_see_or_edit(client: Client) -> None:
    client.as_user("priya")
    ref = ref_from(client.post("/demands/new", data=form(action="draft")))
    client.as_user("neha")
    assert client.get(f"/demands/{ref}").status_code == 404
    assert client.get(f"/demands/{ref}/edit").status_code == 404
    client.as_user("sanjay")  # leadership sees, can't raise or edit
    assert client.get(f"/demands/{ref}").status_code == 200
    assert client.get(f"/demands/{ref}/edit").status_code == 403
    assert client.get("/demands/new").status_code == 403


def test_bu_reader_sees_but_cannot_edit(client: Client) -> None:
    client.as_user("neha")
    ref = ref_from(client.post("/demands/new", data=form(action="draft")))
    client.as_user("rahul")  # own + CARDS read-only
    page = client.get(f"/demands/{ref}")
    assert page.status_code == 200 and f"/demands/{ref}/edit" not in page.text
    assert client.get(f"/demands/{ref}/edit").status_code == 403


def test_job_description_upload_and_download(priya: Client, client: Client) -> None:
    files = {"jd": ("Java JD.pdf", b"%PDF-1.4 fake", "application/pdf")}
    r = priya.post("/demands/new", data=form(action="draft"), files=files)
    ref = ref_from(r)
    got = priya.get(f"/demands/{ref}/jd")
    assert got.status_code == 200 and got.content == b"%PDF-1.4 fake"
    assert client.as_user("neha").get(f"/demands/{ref}/jd").status_code == 404


def test_job_description_type_checked(priya: Client, db: Session) -> None:
    files = {"jd": ("run.exe", b"MZ", "application/octet-stream")}
    r = priya.post("/demands/new", data=form(action="draft"), files=files)
    assert r.status_code == 400 and "Job description not saved" in r.text
    assert db.scalar(select(Demand).where(Demand.app_ref == "DM-000152")) is None
