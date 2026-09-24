"""Two wiring fixes (24 Sep):
1. An incorrect demand goes back to its owner to correct, and an old requisition ID left in the DP
   sheet no longer drives the demand.
2. Panel feedback moves the demand's stage the day it's recorded; a lagging DP sheet doesn't undo it."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import mail
from app.core.security import Actor, actor_from_user
from app.models import Account, Demand, Escalation, ExcelRow, GtdSubmission, OfferApproval, StageEvent, User
from app.services import escalation_service, notify_service
from app.services import interview_service as svc
from app.services.interview_service import Feedback
from seed import sample_sheet
from tests.conftest import Client, user_id


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def actor(db: Session, who: str) -> Actor:
    return actor_from_user(db, db.get_one(User, user_id(who)))


def by_ref(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def import_sample(client: Client, day: date, title: bool = True) -> None:
    r = client.as_user("farah").post(
        "/imports",
        data={"sheet_date": day.isoformat()},
        files={"file": ("dp.xlsx", sample_sheet.build(title=title), "application/octet-stream")},
    )
    assert r.status_code == 303, r.text


def fb(round_: str = "L1", outcome: str = "select", **kw: object) -> Feedback:
    ratings = {"Technical depth": 4, "Problem solving": 4, "Communication": 4}
    kw.setdefault("comments", "notes")
    return Feedback(round=round_, ratings=ratings, outcome=outcome, **kw)  # type: ignore[arg-type]


# --- 1. Incorrect demand: back to the owner, and old IDs ignored ----------------------------------


def test_incorrect_demand_goes_back_to_its_owner(client: Client, db: Session) -> None:
    esc = db.scalar(
        select(Escalation).where(
            Escalation.demand_id == by_ref(db, "DM-000133").id, Escalation.type == "incorrect"
        )
    )
    assert esc is not None
    account = db.get_one(Account, 1)
    actions = [a.value for a in escalation_service.allowed_actions(db, account, esc, by_ref(db, "DM-000133"))]
    assert actions[:2] == ["return", "resubmit"]

    escalation_service.resolve(
        db,
        actor(db, "farah"),
        esc.id,
        reason="Failed GTD basic checks",
        action="return",
        comment="Grade should be C2 and add Kafka",
    )
    d = by_ref(db, "DM-000133")
    assert d.status == "returned"
    [m] = [m for m in mail.sent if m.to == ["arjun.d@example.com"]]
    assert "correct and resubmit" in m.subject.lower() and "Grade should be C2" in m.text

    owner = client.as_user("arjun")
    page = owner.get("/demands/DM-000133").text
    assert "Sent back for correction" in page and "Grade should be C2" in page and "Resubmit to admin" in page
    listing = owner.get("/api/demands").json()
    assert next(x for x in listing if x["app_ref"] == "DM-000133")["needs_attention"] is True

    form = {
        "name": d.name,
        "practice": "DMN-FS",
        "grade": "C2",
        "category": "Open",
        "type": "New",
        "position_type": "Billable",
        "primary_skills": "Spark, AWS, Kafka",
        "start_date": (date.today() + timedelta(days=30)).isoformat(),
        "region": "US",
        "location": "Chicago",
        "work_mode": "Hybrid",
        "action": "submit",
    }
    r = owner.post("/demands/DM-000133/edit", data=form)
    assert r.status_code == 303, r.text
    d = by_ref(db, "DM-000133")
    assert (d.status, d.grade) == ("submitted", "C2")

    notify_service.send_daily_admin_mail(db, 1, force=True)
    assert any("DM-000133" in m.text and "resubmit, was M2PX6D" in m.text for m in mail.sent)
    client.as_user("farah").post(f"/gtd-queue/{d.id}/link", data={"gtd_req_id": "FIXED1"})
    d = by_ref(db, "DM-000133")
    old = db.scalars(select(GtdSubmission).where(GtdSubmission.gtd_req_id == "M2PX6D")).one()
    assert d.gtd_req_id == "FIXED1" and d.submissions[0].previous_submission_id == old.id


def test_old_requisition_id_left_in_the_sheet_is_ignored(client: Client, db: Session) -> None:
    # DM-000133 was incorrect as M2PX6D; it has been corrected and re-entered on GTD as FIXED2.
    d = by_ref(db, "DM-000133")
    d.status = "submitted"
    db.commit()
    client.as_user("farah").post(f"/gtd-queue/{d.id}/link", data={"gtd_req_id": "FIXED2"})
    db.execute(
        update(Escalation)
        .where(Escalation.demand_id == d.id)
        .values(status="resolved", reason="Unknown", action="return", resolved_at=datetime.now(UTC))
    )
    db.commit()

    import_sample(client, date.today())  # still lists M2PX6D as "In Correct Demnad"
    d = by_ref(db, "DM-000133")
    assert d.status == "sent_to_gtd"  # not flipped back to incorrect
    assert (
        db.scalar(select(Escalation).where(Escalation.demand_id == d.id, Escalation.status == "open")) is None
    )
    row = db.scalars(
        select(ExcelRow).where(ExcelRow.gtd_req_id == "M2PX6D").order_by(ExcelRow.id.desc())
    ).first()
    assert row is not None and row.outcome == "superseded" and "replaced by FIXED2" in (row.note or "")
    assert "Replaced requisition IDs" in client.get("/reconciliation").text


# --- 2. Panel feedback moves the stage ---------------------------------------------------------


def test_panel_feedback_moves_the_stage_the_same_day(db: Session) -> None:
    account = db.get_one(Account, 1)
    d = by_ref(db, "DM-000146")  # coverage required; client interview required (default)
    c = svc.ensure_candidate(db, 1, d, "Panel Person")
    svc.record_feedback(
        db, account, user_id("vikram"), c, fb(needs_next_round=True, next_round_note="Design")
    )
    assert by_ref(db, "DM-000146").status == "interviewing"

    req = db.scalars(
        select(svc.Interview).where(svc.Interview.candidate_id == c.id, svc.Interview.round == "L2")
    ).one()
    svc.decide_next_round(db, actor(db, "neha"), req.id, True, None)
    svc.record_feedback(db, account, user_id("anita"), c, fb("L2"), db.get_one(svc.Interview, req.id))
    d = by_ref(db, "DM-000146")
    assert d.status == "panel_selected"
    [m] = [m for m in mail.sent if m.to == ["neha.t@example.com"] and "selected by the panel" in m.subject]
    assert "client interview next" in m.text
    events = db.scalars(select(StageEvent).where(StageEvent.demand_id == d.id).order_by(StageEvent.id)).all()
    assert [e.to_stage for e in events][-2:] == ["interviewing", "panel_selected"]
    assert all(e.origin == "app" for e in events[-2:])


def test_final_panel_decision_goes_straight_to_offer(db: Session) -> None:
    d = by_ref(db, "DM-000146")
    d.client_interview_required = False
    db.commit()
    c = svc.ensure_candidate(db, 1, d, "Final Say", channel="sogeti")
    svc.record_feedback(db, db.get_one(Account, 1), user_id("vikram"), c, fb())
    assert by_ref(db, "DM-000146").status == "offer_in_process"
    offer = db.scalars(select(OfferApproval).where(OfferApproval.demand_id == d.id)).one()
    assert offer.channel == "sogeti" and offer.route is not None  # priced from the rate card
    assert any("offer next" in m.text for m in mail.sent if m.to == ["neha.t@example.com"])


def test_all_rejected_falls_back_to_coverage(db: Session) -> None:
    account = db.get_one(Account, 1)
    d = by_ref(db, "DM-000146")
    c = svc.ensure_candidate(db, 1, d, "Nope One")
    svc.record_feedback(
        db, account, user_id("vikram"), c, fb(outcome="hold", comments="waiting on notice period")
    )
    assert by_ref(db, "DM-000146").status == "interviewing"
    later = db.scalars(select(svc.Interview).where(svc.Interview.candidate_id == c.id)).one()
    later.status = "scheduled"
    later.outcome = later.submitted_at = None
    db.commit()
    svc.record_feedback(db, account, user_id("vikram"), c, fb(outcome="reject", comments="gaps"), later)
    assert by_ref(db, "DM-000146").status == "coverage_required"


def test_lagging_sheet_does_not_pull_progress_back(client: Client, db: Session) -> None:
    d = by_ref(db, "DM-000146")
    svc.record_feedback(
        db, db.get_one(Account, 1), user_id("vikram"), svc.ensure_candidate(db, 1, d, "Keep Me"), fb()
    )
    assert by_ref(db, "DM-000146").status == "panel_selected"
    import_sample(client, date.today())  # 8NTHV6 still "Coverage Required" in the sheet
    assert by_ref(db, "DM-000146").status == "panel_selected"
    assert "Sheet behind the panel" in client.as_user("farah").get("/reconciliation").text


def test_sheet_ahead_still_wins(client: Client, db: Session) -> None:
    import_sample(client, date.today())
    # DM-000144 is "Profiles with client" in the sheet; its panel interview is still scheduled.
    assert by_ref(db, "DM-000144").status == "profiles_with_client"


def test_interview_progress_counts_against_aging(db: Session) -> None:
    d = by_ref(db, "DM-000146")
    db.execute(
        update(StageEvent)
        .where(StageEvent.demand_id == d.id)
        .values(at=datetime.now(UTC) - timedelta(days=30))
    )
    db.execute(
        update(Escalation).values(
            status="resolved", reason="Unknown", action="close", resolved_at=datetime.now(UTC)
        )
    )
    db.commit()
    svc.record_feedback(
        db,
        db.get_one(Account, 1),
        user_id("vikram"),
        svc.ensure_candidate(db, 1, d, "Active Person"),
        fb(outcome="hold", comments="notice"),
    )
    escalation_service.sweep(db, 1, datetime.now(UTC))
    assert (
        db.scalar(select(Escalation).where(Escalation.demand_id == d.id, Escalation.type == "aging")) is None
    )


def test_demands_list_shows_panel_progress(client: Client, db: Session) -> None:
    d = by_ref(db, "DM-000146")
    svc.record_feedback(
        db, db.get_one(Account, 1), user_id("vikram"), svc.ensure_candidate(db, 1, d, "Shown Here"), fb()
    )
    row = next(x for x in client.as_user("neha").get("/api/demands").json() if x["app_ref"] == "DM-000146")
    assert row["status_label"] == "Selected by panel"
    assert "Shown Here selected by the panel at L1 · client interview next" in client.get("/demands").text


def test_client_interview_choice_on_the_form(client: Client, db: Session) -> None:
    form = {
        "name": "Panel-final role",
        "practice": "CCA-FS",
        "grade": "C1",
        "category": "Open",
        "type": "New",
        "position_type": "Billable",
        "client_interview_required": "false",
        "primary_skills": "Java",
        "start_date": (date.today() + timedelta(days=30)).isoformat(),
        "region": "US",
        "location": "Chicago",
        "work_mode": "Hybrid",
        "action": "draft",
    }
    r = client.as_user("priya").post("/demands/new", data=form)
    ref = r.headers["location"].split("/")[2].split("?")[0]
    assert by_ref(db, ref).client_interview_required is False
    assert "panel decision is final" in client.get(f"/demands/{ref}").text


def test_sheet_leaves_a_returned_demand_alone(client: Client, db: Session) -> None:
    # The owner is correcting DM-000133; the next sheet still lists M2PX6D as incorrect.
    esc = db.scalar(
        select(Escalation).where(
            Escalation.demand_id == by_ref(db, "DM-000133").id, Escalation.type == "incorrect"
        )
    )
    assert esc is not None
    escalation_service.resolve(
        db, actor(db, "farah"), esc.id, reason="Failed GTD basic checks", action="return", comment=None
    )
    import_sample(client, date.today())
    d = by_ref(db, "DM-000133")
    assert d.status == "returned"
    assert (
        db.scalar(select(Escalation).where(Escalation.demand_id == d.id, Escalation.status == "open")) is None
    )
    assert "Being resubmitted, ignored" in client.as_user("farah").get("/reconciliation").text
