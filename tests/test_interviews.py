"""Flow 5: interviews, re-centred (decisions.md "Answers before Phase 6"). Phase 6 exit: an interviewer
submits feedback from the mail link without knowing the requisition ID; three rejections escalate."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.config import get_settings
from app.core.security import Actor, actor_from_user
from app.models import Account, Candidate, Demand, Escalation, Interview, InterviewerProfile, User
from app.services import escalation_service
from app.services import interview_service as svc
from app.services.interview_service import Feedback, InterviewError
from seed import sample_sheet
from tests.conftest import GOOD_FORM, RATINGS, Client, user_id

GOOD = GOOD_FORM


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def actor(db: Session, who: str) -> Actor:
    return actor_from_user(db, db.get_one(User, user_id(who)))


def by_ref(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def cand(db: Session, name: str, ref: str | None = None) -> Candidate:
    db.expire_all()
    stmt = select(Candidate).where(Candidate.name == name)
    stmt = stmt.where(Candidate.demand_id == by_ref(db, ref).id) if ref else stmt
    return db.scalars(stmt).one()


def import_sample(client: Client, day: date | None = None, title: bool = True) -> None:
    r = client.as_user("farah").post(
        "/imports",
        data={"sheet_date": (day or date.today()).isoformat()},
        files={"file": ("dp.xlsx", sample_sheet.build(title=title), "application/octet-stream")},
    )
    assert r.status_code == 303, r.text


def fb(**kw: object) -> Feedback:
    base: dict[str, object] = {
        "round": "L1",
        "ratings": dict(RATINGS),
        "outcome": "select",
        "comments": "ok",
    }
    base.update(kw)
    return Feedback(**base)  # type: ignore[arg-type]


# --- Candidates and their requisitions ----------------------------------------------------------


def test_split_names() -> None:
    assert svc.split_names("1. Asha Rao\n2) Ben Li\n- Asha Rao\n0\n\n  ") == ["Asha Rao", "Ben Li"]
    assert svc.split_names(None) == []


def test_sheet_names_put_candidates_on_their_requisition(client: Client, db: Session) -> None:
    import_sample(client)
    a = cand(db, "Candidate A", "DM-000121")  # DIT7AF, offer in process
    assert a.demand_id == by_ref(db, "DM-000121").id and a.source == "sheet" and a.channel == "subcon_vms"
    import_sample(client, date.today() + timedelta(days=1), title=False)  # again: no duplicates
    assert (
        len(db.scalars(select(Candidate).where(Candidate.demand_id == by_ref(db, "DM-000121").id)).all()) == 1
    )


def test_search_finds_by_rough_name(db: Session) -> None:
    hits = svc.search(db, 1, "candidat a")
    assert hits and hits[0].candidate.name == "Candidate A" and hits[0].demand is not None


def test_interviewers_sharing_a_technology_are_alerted_once(client: Client, db: Session) -> None:
    import_sample(client)
    to_vikram = [m for m in mail.sent if m.to == ["vikram.p@example.com"]]
    to_anita = [m for m in mail.sent if m.to == ["anita.g@example.com"]]
    assert len(to_vikram) == 1 and "2ZT7KP" in to_vikram[0].text  # Java, Spring Boot, AWS
    assert len(to_anita) == 1 and "1UT9PL" in to_anita[0].text  # Mainframe, COBOL
    assert "2ZT7KP" not in to_anita[0].text
    assert by_ref(db, "DM-000142").interviewers_alerted_at is not None
    mail.sent.clear()
    import_sample(client, date.today() + timedelta(days=1), title=False)
    assert not [m for m in mail.sent if "new requisition" in m.subject]


# --- Recording a recommendation -----------------------------------------------------------------


def test_feedback_on_an_assigned_interview(client: Client, db: Session) -> None:
    iv = db.scalars(select(Interview).where(Interview.interviewer_id == user_id("vikram"))).first()
    assert iv is not None and iv.status == "scheduled"
    r = client.as_user("vikram").post(f"/interviews/{iv.id}/feedback", data=GOOD)
    assert "msg=Feedback" in r.headers["location"]
    db.expire_all()
    done = db.get_one(Interview, iv.id)
    assert (done.status, done.outcome, done.feedback_token) == ("completed", "select", None)
    assert done.ratings == dict(RATINGS)


def test_record_for_a_candidate_found_by_name_and_map_it(client: Client, db: Session) -> None:
    client.as_user("vikram")
    r = client.post("/interviews/add-candidate", data={"name": "Priyanka Walk-in", "demand_id": ""})
    c = cand(db, "Priyanka Walk-in")
    assert c.demand_id is None and r.headers["location"] == f"/interviews/record/{c.id}"
    d = by_ref(db, "DM-000146")
    client.post(f"/interviews/record/{c.id}", data={**GOOD, "round": "L1", "demand_id": str(d.id)})
    c = cand(db, "Priyanka Walk-in")
    assert c.demand_id == d.id and c.current_stage == "L1 select"
    iv = db.scalars(select(Interview).where(Interview.candidate_id == c.id)).one()
    assert iv.demand_id == d.id and iv.interviewer_id == user_id("vikram")
    assert client.get("/demands/DM-000146").status_code == 200  # now visible to this interviewer
    assert client.as_user("anita").get("/demands/DM-000146").status_code == 404


def test_unmapped_recommendation_waits_for_the_admin_team(client: Client, db: Session) -> None:
    client.as_user("vikram").post("/interviews/add-candidate", data={"name": "Unknown Req Person"})
    c = cand(db, "Unknown Req Person")
    client.post(f"/interviews/record/{c.id}", data={**GOOD, "round": "L1"})
    team = client.as_user("farah")
    assert "Unknown Req Person" in team.get("/candidates?tab=unmapped").text
    team.post(f"/candidates/{c.id}/map", data={"demand_id": str(by_ref(db, "DM-000142").id)})
    c = cand(db, "Unknown Req Person")
    assert c.demand_id == by_ref(db, "DM-000142").id
    assert db.scalars(select(Interview).where(Interview.candidate_id == c.id)).one().demand_id == c.demand_id


def test_mapping_onto_a_requisition_with_the_same_person_merges(client: Client, db: Session) -> None:
    team = client.as_user("farah")
    team.post("/candidates/add", data={"name": "candidate a"})  # lower case, no requisition
    loose = cand(db, "candidate a")
    team.post(f"/candidates/{loose.id}/map", data={"demand_id": str(by_ref(db, "DM-000142").id)})
    assert (
        len(db.scalars(select(Candidate).where(Candidate.demand_id == by_ref(db, "DM-000142").id)).all()) == 1
    )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"ratings": {"Technical Skills": 4}}, "Rate 1 to 10"),
        ({"ratings": RATINGS | {"Communication": 11}}, "Rate 1 to 10: Communication"),
        ({"bands": {"Technical Skills": "Good"}}, "Choose a band for"),
        ({"outcome": "maybe"}, "offer, reject or hold"),
        ({"outcome": "reject", "comments": ""}, "needs support, or add a remark"),
        ({"support_area": "Juggling"}, "area the support is needed in"),
        ({"designation": "Z9"}, "recommended designation"),
        ({"round": "L3"}, "round"),
        ({"outcome": "reject", "needs_next_round": True}, "rejected candidate"),
        ({"round": "L2", "needs_next_round": True}, "last round"),
    ],
)
def test_feedback_validation(db: Session, change: dict[str, object], message: str) -> None:
    c = cand(db, "Candidate A", "DM-000142")
    with pytest.raises(InterviewError, match=message):
        svc.record_feedback(db, db.get_one(Account, 1), user_id("vikram"), c, fb(**change))


# --- L2: panelist asks, demand owner approves, staffing schedules --------------------------------


def test_l2_request_approval_and_scheduling(client: Client, db: Session) -> None:
    c = svc.ensure_candidate(db, 1, by_ref(db, "DM-000142"), "Candidate Z")
    db.commit()
    svc.record_feedback(
        db,
        db.get_one(Account, 1),
        user_id("vikram"),
        c,
        fb(needs_next_round=True, next_round_note="Check system design"),
    )
    req = db.scalars(select(Interview).where(Interview.candidate_id == c.id, Interview.round == "L2")).one()
    assert req.status == "requested" and req.requested_by == user_id("vikram")
    owner_mail = [m for m in mail.sent if m.to == ["priya.n@example.com"]]
    assert owner_mail and "L2 requested for Candidate Z" in owner_mail[0].subject

    page = client.as_user("priya").get("/demands/DM-000142").text
    assert "Approve L2" in page and "Check system design" in page
    r = client.post(f"/demands/DM-000142/rounds/{req.id}", data={"decision": "decline", "note": ""})
    assert "err=" in r.headers["location"]  # declining needs a note
    assert client.as_user("neha").post(
        f"/demands/DM-000142/rounds/{req.id}", data={"decision": "approve"}
    ).status_code in (303, 404)
    db.expire_all()
    assert db.get_one(Interview, req.id).status == "requested"  # not neha's demand
    client.as_user("priya").post(f"/demands/DM-000142/rounds/{req.id}", data={"decision": "approve"})
    db.expire_all()
    assert db.get_one(Interview, req.id).status == "open" and db.get_one(
        Interview, req.id
    ).decided_by == user_id("priya")

    # Staffing arranged it; the GTD admin team records it with no interviewer yet, then assigns one.
    team = client.as_user("farah")
    assert "Candidate Z" in team.get("/candidates?tab=to_schedule").text
    team.post(
        f"/candidates/{c.id}/schedule",
        data={"interview_id": str(req.id), "round": "L2", "interviewer_id": ""},
    )
    db.expire_all()
    assert db.get_one(Interview, req.id).status == "open"
    mail.sent.clear()
    team.post(
        f"/candidates/{c.id}/schedule",
        data={
            "interview_id": str(req.id),
            "round": "L2",
            "interviewer_id": str(user_id("anita")),
            "when": "2026-10-02T10:30",
        },
    )
    db.expire_all()
    iv = db.get_one(Interview, req.id)
    assert iv.status == "scheduled" and iv.interviewer_id == user_id("anita") and iv.feedback_token
    [invite] = mail.sent
    assert invite.to == ["anita.g@example.com"]
    assert invite.subject == "[2ZT7KP | DM-000142] L2 – Candidate Z"
    assert f"/feedback/{iv.feedback_token}" in invite.text and "10:30" in invite.text


def test_admin_demand_owner_can_decide_a_round(db: Session) -> None:
    c = svc.ensure_candidate(db, 1, by_ref(db, "DM-000142"), "Candidate Y")
    svc.record_feedback(db, db.get_one(Account, 1), user_id("vikram"), c, fb(needs_next_round=True))
    req = db.scalars(select(Interview).where(Interview.candidate_id == c.id, Interview.round == "L2")).one()
    svc.decide_next_round(db, actor(db, "kavya"), req.id, True, None)
    assert db.get_one(Interview, req.id).status == "open"


# --- Phase 6 exit ---------------------------------------------------------------------------------


def test_phase6_exit_feedback_from_the_mail_link_without_the_requisition(client: Client, db: Session) -> None:
    iv = db.scalars(select(Interview).where(Interview.interviewer_id == user_id("vikram"))).first()
    assert iv is not None and iv.feedback_token
    anonymous = Client(client.app, follow_redirects=False)  # no sign-in, no View-as cookie
    page = anonymous.get(f"/feedback/{iv.feedback_token}")
    assert page.status_code == 200 and "Candidate" in page.text and "2ZT7KP" in page.text
    r = anonymous.post(f"/feedback/{iv.feedback_token}", data=GOOD)
    assert r.headers["location"].startswith("/feedback-done")
    db.expire_all()
    assert db.get_one(Interview, iv.id).status == "completed"
    token = iv.feedback_token
    again = anonymous.get(f"/feedback/{token}")
    assert again.status_code == 404 and "This link is closed" in again.text


def test_phase6_exit_three_rejections_escalate(db: Session) -> None:
    account = db.get_one(Account, 1)
    d = by_ref(db, "DM-000146")
    for name in ("Rej One", "Rej Two"):
        c = svc.ensure_candidate(db, 1, d, name)
        svc.record_feedback(db, account, user_id("vikram"), c, fb(outcome="reject", comments="Gaps in AWS"))
    escalation_service.sweep(db, 1, datetime.now(UTC))
    assert (
        db.scalar(
            select(Escalation).where(Escalation.demand_id == d.id, Escalation.type == "rejection_limit")
        )
        is None
    )
    c = svc.ensure_candidate(db, 1, d, "Rej Three")
    svc.record_feedback(db, account, user_id("vikram"), c, fb(outcome="reject", comments="No Spring"))
    escalation_service.sweep(db, 1, datetime.now(UTC))
    esc = db.scalar(
        select(Escalation).where(Escalation.demand_id == d.id, Escalation.type == "rejection_limit")
    )
    assert esc is not None and "3 candidates rejected" in (esc.detail or "")


def test_panel_sla_escalates_until_feedback_is_in(db: Session) -> None:
    iv = db.scalars(select(Interview).where(Interview.interviewer_id == user_id("vikram"))).first()
    assert iv is not None
    iv.scheduled_at = datetime.now(UTC) - timedelta(hours=49)
    db.commit()
    escalation_service.sweep(db, 1, datetime.now(UTC))
    esc = db.scalar(
        select(Escalation).where(Escalation.demand_id == iv.demand_id, Escalation.type == "panel_sla")
    )
    assert esc is not None and "no feedback after 48 h" in (esc.detail or "")
    account = db.get_one(Account, 1)
    d = db.get_one(Demand, iv.demand_id)
    assert not escalation_service.is_cleared(db, account, esc, d, datetime.now(UTC))
    svc.record_feedback(db, account, user_id("vikram"), db.get_one(Candidate, iv.candidate_id), fb(), iv)
    assert escalation_service.is_cleared(db, account, esc, d, datetime.now(UTC))


# --- CVs, profiles, Karat, guards ---------------------------------------------------------------


def test_cv_from_staffing_email_and_link_for_the_panel(client: Client, db: Session) -> None:
    iv = db.scalars(select(Interview).where(Interview.interviewer_id == user_id("vikram"))).first()
    assert iv is not None
    team = client.as_user("farah")
    team.post(f"/candidates/{iv.candidate_id}/cv", files={"cv": ("cv.pdf", b"%PDF cv", "application/pdf")})
    assert team.get(f"/candidates/{iv.candidate_id}/cv").content == b"%PDF cv"
    anonymous = Client(client.app)
    assert anonymous.get(f"/cv/{iv.feedback_token}").content == b"%PDF cv"
    assert anonymous.get("/cv/not-a-real-token-at-all-xxxxx").status_code == 404
    r = team.post(
        f"/candidates/{iv.candidate_id}/cv", files={"cv": ("cv.exe", b"MZ", "application/octet-stream")}
    )
    assert "err=" in r.headers["location"]


def test_interviewer_profiles_screen(client: Client, db: Session) -> None:
    admin = client.as_user("kavya")
    assert "Vikram P." in admin.get("/interviewers").text
    admin.post(
        f"/interviewers/{user_id('anita')}",
        data={"skills": "Mainframe, Java", "max_grade": "C2", "active": "1", "practices": ["DMN-FS"]},
    )
    db.expire_all()
    p = db.get_one(InterviewerProfile, user_id("anita"))
    assert p.skills == ["Mainframe", "Java"] and p.max_grade == "C2"
    matching = {u.name for u in svc.matching_interviewers(db, 1, by_ref(db, "DM-000142"))}
    assert matching == {"Vikram P.", "Anita G."}  # Anita now shares Java with the requisition


def test_karat_integration_stub(client: Client, db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    body = {
        "external_ref": "K-100",
        "candidate_name": "Karat Person",
        "gtd_req_id": "2ZT7KP",
        "round": "L1",
        "outcome": "select",
        "report_url": "https://karat.example/report/100",
    }
    assert client.post("/api/integrations/karat/results", json=body).status_code == 404  # not configured
    monkeypatch.setenv("KARAT_API_KEY", "secret-key")
    get_settings.cache_clear()
    try:
        assert (
            client.post(
                "/api/integrations/karat/results", json=body, headers={"X-Api-Key": "nope"}
            ).status_code
            == 401
        )
        r = client.post("/api/integrations/karat/results", json=body, headers={"X-Api-Key": "secret-key"})
        assert r.status_code == 201 and r.json()["mapped_to_requisition"] is True
        again = client.post("/api/integrations/karat/results", json=body, headers={"X-Api-Key": "secret-key"})
        assert again.json()["interview_id"] == r.json()["interview_id"]  # idempotent
        loose = body | {"external_ref": "K-101", "gtd_req_id": None, "candidate_name": "Karat Loose"}
        assert (
            client.post(
                "/api/integrations/karat/results", json=loose, headers={"X-Api-Key": "secret-key"}
            ).json()["mapped_to_requisition"]
            is False
        )
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    c = cand(db, "Karat Person")
    iv = db.scalars(select(Interview).where(Interview.candidate_id == c.id)).one()
    assert (iv.source, iv.status, iv.report_url) == ("karat", "completed", "https://karat.example/report/100")


@pytest.mark.parametrize(
    ("who", "path", "code"),
    [
        ("priya", "/interviews", 403),
        ("priya", "/candidates", 403),
        ("vikram", "/candidates", 403),
        ("farah", "/interviewers", 403),
        ("sanjay", "/candidates", 403),
        ("farah", "/candidates", 200),
        ("vikram", "/interviews", 200),
    ],
)
def test_guards(client: Client, who: str, path: str, code: int) -> None:
    assert client.as_user(who).get(path).status_code == code


def test_my_interviews_page(client: Client) -> None:
    page = client.as_user("vikram").get("/interviews?q=candidate").text
    assert "Candidate A" in page and "Submit feedback" in page and "New in your skill area" in page


def test_interviewer_sees_every_open_requisition_and_filters_by_technology(
    client: Client, db: Session
) -> None:
    from app.models import Demand

    d = db.scalars(select(Demand).where(Demand.app_ref == "DM-000121")).one()  # Salesforce, offer stage
    page = client.as_user("vikram").get("/interviews").text
    assert "All open requisitions" in page and "DM-000121" in page and d.name in page
    assert "$" not in page.split("All open requisitions")[1].split("</section>")[0]  # no rates here

    only = client.as_user("vikram").get("/interviews?tech=salesforce").text
    part = only.split("All open requisitions")[1].split("</section>")[0]
    assert "DM-000121" in part and "Clear</a>" in part
    shown = svc.with_technology(svc.open_requisitions(db, 1), "Salesforce")
    assert shown and all(
        "salesforce" in [t.lower() for t in x.primary_skills + x.secondary_skills] for x in shown
    )
    assert part.count('class="open-req"') == len(shown) < len(svc.open_requisitions(db, 1))
    none = client.as_user("vikram").get("/interviews?tech=Cobol-9000").text
    assert "No open requisition needs Cobol-9000" in none


def test_interviewer_is_shown_their_overdue_feedback(client: Client, db: Session) -> None:
    account = db.get_one(Account, 1)
    d = db.scalars(select(Demand).where(Demand.app_ref == "DM-000146")).one()
    c = svc.ensure_candidate(db, 1, d, "Late Feedback")
    db.add(
        Interview(
            candidate_id=c.id, demand_id=d.id, interviewer_id=user_id("vikram"), round="L1",
            status="scheduled",
            scheduled_at=datetime.now(UTC) - timedelta(hours=account.panel_timer_hours + 5),
        )
    )  # fmt: skip
    db.commit()
    page = client.as_user("vikram").get("/interviews").text
    assert "Feedback overdue on" in page and "Feedback overdue · escalated" in page
    assert "Feedback overdue on" not in client.as_user("anita").get("/interviews").text
