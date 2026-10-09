"""The demand owner runs their demand: edits it at any stage, answers escalations with a reason and
fixes them on the demand, closes it, and types its job description."""

from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.enums import DemandStatus, EscalationType
from app.models import Account, Demand, Escalation
from app.services import escalation_service
from tests.conftest import Client

LATE = "DM-000121"  # Priya's, past its start date, with an open past-start escalation


def demand(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def past_start(db: Session) -> Escalation:
    d = demand(db, LATE)
    stmt = select(Escalation).where(
        Escalation.demand_id == d.id, Escalation.type == EscalationType.PAST_START.value
    )
    return db.scalars(stmt).one()


def form_of(d: Demand, **over: str) -> dict[str, str]:
    """The edit form as it would post for this demand."""
    data = {
        "name": d.name, "practice": d.practice or "", "grade": d.grade or "", "category": d.category,
        "type": d.type, "position_type": d.position_type,
        "client_interview_required": "true" if d.client_interview_required else "false",
        "primary_skills": ", ".join(d.primary_skills), "secondary_skills": ", ".join(d.secondary_skills),
        "exp_min": "" if d.exp_min is None else str(d.exp_min),
        "exp_max": "" if d.exp_max is None else str(d.exp_max),
        "client_rate": "", "start_date": d.start_date.isoformat() if d.start_date else "",
        "region": d.region or "", "location": d.location or "", "work_mode": d.work_mode or "",
        "hiring_manager": d.hiring_manager or "", "positions": "1", "jd_text": d.jd_text or "",
        "action": "save",
    }  # fmt: skip
    return data | over


def test_a_reason_keeps_the_escalation_open_until_the_demand_is_fixed(client: Client, db: Session) -> None:
    esc = past_start(db)
    c = client.as_user("priya")
    page = c.get(f"/demands/{LATE}").text
    assert "Needs your response" in page and "What will you do?" not in page
    assert 'name="action"' not in page.split("Needs your response")[1].split("</form>")[0]
    r = c.post(f"/escalations/{esc.id}/resolve",
               data={"reason": "Offer in progress", "comment": "", "next": f"/demands/{LATE}"})  # fmt: skip
    assert "Reason%20recorded" in r.headers["location"]
    db.expire_all()
    esc = db.get_one(Escalation, esc.id)
    assert esc.status == "open" and esc.due_at > datetime.now(UTC)  # still open, clock restarted
    # no reason, no response
    bad = c.post(f"/escalations/{esc.id}/resolve", data={"reason": "", "next": f"/demands/{LATE}"})
    assert "err=" in bad.headers["location"]
    # fixing it on the demand closes it: the owner moves the start date with Edit
    d = demand(db, LATE)
    new = date.today() + timedelta(days=21)
    mail.sent.clear()
    r = c.post(f"/demands/{LATE}/edit", data=form_of(d, start_date=new.isoformat()))
    assert r.status_code == 303, r.text[-400:]
    db.expire_all()
    esc = db.get_one(Escalation, esc.id)
    assert esc.status == "resolved" and esc.reason == escalation_service.BY_EDIT
    assert demand(db, LATE).start_date == new and demand(db, LATE).status == "offer_in_process"
    [m] = [m for m in mail.sent if "Demand changed by Priya N." in m.subject]
    assert "Start date:" in m.text and "kavya.r@example.com" in m.to


def test_an_answered_escalation_closes_itself_at_the_next_check(client: Client, db: Session) -> None:
    esc = past_start(db)
    client.as_user("priya").post(f"/escalations/{esc.id}/resolve", data={"reason": "Offer in progress"})
    d = demand(db, LATE)
    d.start_date = date.today() + timedelta(days=30)  # changed some other way, e.g. by the team lead
    db.commit()
    escalation_service.sweep(db, 1, datetime.now(UTC))
    db.expire_all()
    assert db.get_one(Escalation, esc.id).status == "resolved"


def test_the_team_lead_can_edit_any_demand_but_others_cannot(client: Client, db: Session) -> None:
    d = demand(db, LATE)
    lead = form_of(d, location="Riverwoods", bu_id=str(d.bu_id))  # the lead picks the business unit
    r = client.as_user("kavya").post(f"/demands/{LATE}/edit", data=lead)
    assert r.status_code == 303 and demand(db, LATE).location == "Riverwoods"
    assert client.as_user("farah").get(f"/demands/{LATE}/edit").status_code == 403  # GTD admin team
    assert client.as_user("neha").get(f"/demands/{LATE}/edit").status_code in (403, 404)  # another owner


def test_a_dropped_demand_is_corrected_and_resubmitted_from_the_edit_form(
    client: Client, db: Session
) -> None:
    d = demand(db, "DM-000146")  # Neha's, sourcing
    d.status = "dropped"
    db.commit()
    acc = db.get_one(Account, 1)
    escalation_service.open_escalation(
        db, acc, d, EscalationType.DROPPED, "Gone from the latest sheet", datetime.now(UTC)
    )
    db.commit()
    c = client.as_user("neha")
    assert "Save and resubmit to the GTD admin team" in c.get("/demands/DM-000146/edit").text
    start = (date.today() + timedelta(days=40)).isoformat()
    r = c.post(
        "/demands/DM-000146/edit", data=form_of(d, name="Corrected", start_date=start, action="submit")
    )
    assert r.status_code == 303, r.text[-400:]
    d = demand(db, "DM-000146")
    assert d.status_enum is DemandStatus.SUBMITTED and d.name == "Corrected"
    left = db.scalars(
        select(Escalation).where(Escalation.demand_id == d.id, Escalation.status == "open")
    ).all()
    assert [e for e in left if e.type == "dropped"] == []


def test_the_owner_closes_a_demand_with_a_reason(client: Client, db: Session) -> None:
    c = client.as_user("priya")
    assert "This position is no longer needed" in c.get(f"/demands/{LATE}").text
    assert "err=" in c.post(f"/demands/{LATE}/close", data={"reason": ""}).headers["location"]
    reason = db.get_one(Account, 1).settings.resolution_reasons[0]
    assert (
        "err="
        in client.as_user("farah").post(f"/demands/{LATE}/close", data={"reason": reason}).headers["location"]
    )
    r = client.as_user("priya").post(f"/demands/{LATE}/close", data={"reason": reason})
    assert "closed" in r.headers["location"]
    d = demand(db, LATE)
    assert d.status_enum is DemandStatus.CLOSED
    assert (
        db.scalars(select(Escalation).where(Escalation.demand_id == d.id, Escalation.status == "open")).all()
        == []
    )
    assert client.as_user("priya").get(f"/demands/{LATE}/edit").status_code == 403  # closed: no more edits


def test_the_job_description_is_typed_on_the_form(client: Client, db: Session) -> None:
    c = client.as_user("priya")
    page = c.get("/demands/new").text
    assert 'name="jd_text"' in page and "Draft it for me" in page
    assert 'type="file" name="jd" accept' not in page.replace(" disabled", "") or "disabled" in page
    d = demand(db, "DM-000150")  # her draft
    text = "JOB DESCRIPTION\n\nRole: Java Full Stack Developer\n\nWhat you will do\n- Build things"
    r = c.post("/demands/DM-000150/edit", data=form_of(d, jd_text=text, action="draft"))
    assert r.status_code == 303, r.text[-400:]
    assert demand(db, "DM-000150").jd_text == text.replace("\\n", "\n")
    shown = c.get("/demands/DM-000150").text
    assert "What you will do" in shown and "- Build things" in shown
    assert "Role: Java Full Stack Developer" in c.get("/demands/DM-000150/jd").text


def test_each_box_is_edited_in_place(client: Client, db: Session) -> None:
    c = client.as_user("priya")
    page = c.get(f"/demands/{LATE}").text
    for part in ("position", "requirements", "commercial"):
        assert f'data-edit="{part}"' in page and f'action="/demands/{LATE}/part/{part}"' in page
    assert f'href="/demands/{LATE}/edit">Edit<' not in page  # no trip to the full form
    before = demand(db, LATE)
    rate, name = before.client_rate, before.name
    r = c.post(
        f"/demands/{LATE}/part/commercial",
        data={
            "location": "Riverwoods",
            "start_date": "2027-02-01",
            "region": "US",
            "work_mode": "Hybrid",
            "client_rate": "",
        },
    )
    assert r.headers["location"].endswith("msg=Saved#commercial")
    d = demand(db, LATE)
    assert d.location == "Riverwoods" and d.start_date == date(2027, 2, 1)
    assert d.client_rate == rate and d.name == name  # what the box doesn't carry is untouched
    c.post(
        f"/demands/{LATE}/part/requirements",
        data={"primary_skills": "Salesforce, Apex", "exp_min": "3", "exp_max": "8", "jd_text": "Typed here"},
    )
    d = demand(db, LATE)
    assert d.primary_skills == ["Salesforce", "Apex"] and (d.exp_min, d.exp_max) == (3, 8)
    assert d.jd_text == "Typed here"
    bad = c.post(f"/demands/{LATE}/part/position", data={"name": "X", "practice": "NOPE", "grade": "C2"})
    assert "err=" in bad.headers["location"] and "edit=position" in bad.headers["location"]
    assert client.as_user("farah").post(f"/demands/{LATE}/part/position", data={}).status_code == 403
    assert client.as_user("priya").post(f"/demands/{LATE}/part/other", data={}).status_code == 404


def test_the_joining_date_is_edited_in_the_box(client: Client, db: Session) -> None:
    ref = "DM-000117"  # Priya's, offer made
    c = client.as_user("priya")
    assert 'name="expected_doj"' in c.get(f"/demands/{ref}").text
    assert 'name="expected_doj"' not in c.get(f"/demands/{LATE}").text  # no offer made yet
    d = demand(db, ref)
    form = {
        "start_date": d.start_date.isoformat(),
        "region": d.region,
        "location": d.location,
        "work_mode": d.work_mode,
        "client_rate": "",
        "expected_doj": "2026-11-02",
    }
    mail.sent.clear()
    r = c.post(f"/demands/{ref}/part/commercial", data=form)
    assert "msg=Saved" in r.headers["location"]
    d = demand(db, ref)
    assert d.expected_doj == date(2026, 11, 2) and d.expected_doj_at is not None
    assert escalation_service.current_doj(db, 1)[d.id] == date(2026, 11, 2)
    [m] = [m for m in mail.sent if "Demand changed" in m.subject]
    assert "Date of joining:" in m.text
    assert "02 Nov 2026" in c.get(f"/demands/{ref}").text
