"""Flow 7: the escalation engine. Phase 4 exit: every trigger in flow-artifact §9.1 (except rejection
limit and panel SLA) opens, promotes and resolves correctly."""

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import mail
from app.core.security import Actor, actor_from_user
from app.models import Account, Demand, Escalation, GtdSubmission, StageEvent, User
from app.services import escalation_service as svc
from app.services import notify_service, reconcile_service
from app.services.escalation_service import EscalationError
from seed import sample_sheet
from tests.conftest import Client, user_id

NOW = datetime.now(UTC)


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def actor(db: Session, who: str) -> Actor:
    return actor_from_user(db, db.get_one(User, user_id(who)))


def by_ref(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def open_esc(db: Session, ref: str, type_: str) -> Escalation | None:
    db.expire_all()
    return db.scalar(
        select(Escalation).where(
            Escalation.demand_id == by_ref(db, ref).id, Escalation.type == type_, Escalation.status == "open"
        )
    )


def resolve_all_seeded(db: Session) -> None:
    """Start from a clean slate so each test sees only the escalations it causes."""
    db.execute(
        update(Escalation).values(status="resolved", reason="Unknown", action="close", resolved_at=NOW)
    )
    db.commit()


# --- Triggers ------------------------------------------------------------------------------------


def test_not_submitted_opens_after_next_working_day_mail_time(db: Session) -> None:
    resolve_all_seeded(db)
    notify_service.send_daily_admin_mail(db, 1, force=True)
    assert by_ref(db, "DM-000151").status == "notified"
    svc.sweep(db, 1, NOW)
    assert open_esc(db, "DM-000151", "not_submitted") is None  # same day: not yet
    svc.sweep(db, 1, NOW + timedelta(days=5))  # always past the next working day's mail time
    esc = open_esc(db, "DM-000151", "not_submitted")
    assert esc is not None and esc.level == 1 and "no GTD requisition ID" in (esc.detail or "")


def test_linked_in_time_means_no_not_submitted(db: Session, client: Client) -> None:
    resolve_all_seeded(db)
    notify_service.send_daily_admin_mail(db, 1, force=True)
    client.as_user("farah").post(
        f"/gtd-queue/{by_ref(db, 'DM-000151').id}/link", data={"gtd_req_id": "ONTIME1"}
    )
    svc.sweep(db, 1, NOW + timedelta(days=5))
    assert open_esc(db, "DM-000151", "not_submitted") is None


def test_aging_opens_after_quiet_period(db: Session) -> None:
    resolve_all_seeded(db)
    d = by_ref(db, "DM-000142")  # coverage required
    db.execute(update(StageEvent).where(StageEvent.demand_id == d.id).values(at=NOW - timedelta(days=13)))
    db.commit()
    svc.sweep(db, 1, NOW)
    assert open_esc(db, "DM-000142", "aging") is None  # 13 days < 14
    svc.sweep(db, 1, NOW + timedelta(days=1))
    esc = open_esc(db, "DM-000142", "aging")
    assert esc is not None and "no stage change since" in (esc.detail or "")


def test_past_start_without_doj(db: Session) -> None:
    resolve_all_seeded(db)
    svc.sweep(db, 1, NOW)
    esc = open_esc(db, "DM-000117", "past_start")  # started 1 Sep, offer in market, no BCM sheet yet
    assert esc is not None and "no DOJ" in (esc.detail or "")
    assert open_esc(db, "DM-000116", "past_start") is None  # staffed
    assert open_esc(db, "DM-000150", "past_start") is None  # draft


def test_past_start_with_late_doj(db: Session, client: Client) -> None:
    resolve_all_seeded(db)
    client.as_user("farah").post(
        "/imports",
        data={"sheet_date": date.today().isoformat()},
        files={"file": ("dp.xlsx", sample_sheet.build(), "application/octet-stream")},
    )
    svc.sweep(db, 1, NOW)
    esc = open_esc(db, "DM-000117", "past_start")
    assert esc is not None and "DOJ 15 Oct" in (esc.detail or "")


def test_reconciliation_triggers_open_at_l1(db: Session, client: Client) -> None:
    client.as_user("farah").post(
        "/imports",
        data={"sheet_date": date.today().isoformat()},
        files={"file": ("dp.xlsx", sample_sheet.build(), "application/octet-stream")},
    )
    esc = open_esc(db, "DM-000148", "missing")
    assert esc is not None and esc.level == 1 and esc.notified_level == 0
    assert [e.kind for e in esc.events] == ["opened"]


def test_sweep_is_idempotent(db: Session) -> None:
    resolve_all_seeded(db)
    first = svc.sweep(db, 1, NOW)
    second = svc.sweep(db, 1, NOW)
    assert first.opened and second.opened == [] and second.mails == 0
    n = db.scalar(select(Escalation.id).where(Escalation.status == "open").order_by(Escalation.id.desc()))
    assert n is not None


# --- Ladder and notifications -----------------------------------------------------------------------


def test_l1_mail_goes_to_the_responsible_person_with_the_steps(db: Session) -> None:
    resolve_all_seeded(db)
    svc.sweep(db, 1, NOW)
    [m] = [m for m in mail.sent if m.to == ["priya.n@example.com"]]  # owner of DM-000117, past its start
    assert "Action needed" in m.subject and "High severity" in m.subject
    assert "DM-000117" in m.text and "Who acts: Demand owner (Priya N.)" in m.text
    assert "Steps: Give a revised start date" in m.text and "Respond by" in m.text
    assert "kavya.r@example.com" in m.cc  # the GTD team admin is informed
    # at L1 nobody above is told: not leadership, not the delivery head
    assert "sanjay.m@example.com" not in m.cc and "ritu.s@example.com" not in m.cc


def test_with_no_one_to_act_it_falls_to_the_gtd_team_admin(db: Session) -> None:
    resolve_all_seeded(db)
    db.get_one(User, user_id("priya")).active = False  # the owner has left
    db.commit()
    svc.sweep(db, 1, NOW)
    assert any(m.to == ["kavya.r@example.com"] and "DM-000117" in m.text for m in mail.sent)
    assert all("priya.n@example.com" not in m.to + m.cc for m in mail.sent)


def test_overdue_becomes_l2_leadership_informed_owner_still_acts_and_is_reminded_daily(db: Session) -> None:
    resolve_all_seeded(db)
    svc.sweep(db, 1, NOW)
    esc = open_esc(db, "DM-000117", "past_start")
    assert esc is not None and esc.level == 1 and esc.severity == "high" and esc.responsible == "demand_owner"
    due = esc.due_at
    mail.sent.clear()
    later = due + timedelta(minutes=1)
    result = svc.sweep(db, 1, later)
    esc = open_esc(db, "DM-000117", "past_start")
    assert esc is not None and esc.level == 2 and esc.notified_level == 2
    assert esc.due_at == due and any("DM-000117" in p for p in result.promoted)
    assert [e.kind for e in esc.events] == ["opened", "notified", "promoted", "notified"]
    [l2] = [m for m in mail.sent if "DM-000117" in m.text]
    assert l2.to == ["priya.n@example.com"] and "Overdue" in l2.subject  # the same person still acts
    for informed in ("sanjay.m@example.com", "ritu.s@example.com", "kavya.r@example.com"):
        assert informed in l2.cc
    assert "copied for information only" in l2.text

    mail.sent.clear()
    assert svc.sweep(db, 1, later + timedelta(hours=2)).mails == 0  # already told today
    svc.sweep(db, 1, later + timedelta(days=1))  # next day: a reminder, to the responsible person only
    [r] = [m for m in mail.sent if "DM-000117" in m.text]
    assert r.to == ["priya.n@example.com"] and r.cc == [] and "Reminder: overdue" in r.subject
    esc = open_esc(db, "DM-000117", "past_start")
    assert esc is not None and esc.level == 2  # there is no L3


def test_failed_mail_is_retried_next_sweep(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    resolve_all_seeded(db)

    def boom(_: mail.Mail) -> None:
        raise ConnectionError("SMTP down")

    monkeypatch.setattr(mail, "send", boom)
    with pytest.raises(ConnectionError):
        svc.sweep(db, 1, NOW)
    db.rollback()
    esc = open_esc(db, "DM-000117", "past_start")
    assert esc is not None and esc.notified_level == 0  # opened, not yet mailed
    monkeypatch.undo()
    assert svc.sweep(db, 1, NOW).mails >= 1
    assert open_esc(db, "DM-000117", "past_start").notified_level == 1  # type: ignore[union-attr]


# --- Resolving ----------------------------------------------------------------------------------


def test_close_demand_resolves_all_its_escalations(db: Session) -> None:
    resolve_all_seeded(db)
    svc.sweep(db, 1, NOW)
    d = by_ref(db, "DM-000121")
    extra = svc.open_escalation(db, db.get_one(Account, 1), d, svc.EscalationType.AGING, "quiet", NOW)
    db.commit()
    esc = open_esc(db, "DM-000121", "past_start")
    assert esc is not None and extra is not None
    svc.resolve(
        db,
        actor(db, "priya"),
        esc.id,
        reason="Withdrawn by client",
        action="close",
        comment="Client pulled it",
    )
    assert by_ref(db, "DM-000121").status == "closed"
    assert open_esc(db, "DM-000121", "aging") is None
    done = db.get_one(Escalation, esc.id)
    assert (done.status, done.action, done.reason, done.resolved_by) == (
        "resolved",
        "close",
        "Withdrawn by client",
        user_id("priya"),
    )
    assert done.events[-1].kind == "resolved"


def test_resubmit_goes_back_into_the_mail_and_chains_the_new_id(db: Session, client: Client) -> None:
    esc = open_esc(db, "DM-000139", "missing")  # seeded: sent as 7QWZ2L, never in the sheet
    assert esc is not None
    svc.resolve(
        db, actor(db, "kavya"), esc.id, reason="Failed GTD basic checks", action="resubmit", comment=None
    )
    assert by_ref(db, "DM-000139").status == "submitted"
    notify_service.send_daily_admin_mail(db, 1, force=True)
    assert any("DM-000139" in m.text and "resubmit, was 7QWZ2L" in m.text for m in mail.sent)
    client.as_user("farah").post(
        f"/gtd-queue/{by_ref(db, 'DM-000139').id}/link", data={"gtd_req_id": "RESUB1"}
    )
    d = by_ref(db, "DM-000139")
    assert d.status == "sent_to_gtd" and d.gtd_req_id == "RESUB1"
    old = db.scalars(select(GtdSubmission).where(GtdSubmission.gtd_req_id == "7QWZ2L")).one()
    assert d.submissions[0].previous_submission_id == old.id


def test_resubmitted_demand_is_not_dropped_for_its_old_id(db: Session, client: Client) -> None:
    team = client.as_user("farah")
    team.post("/imports", data={"sheet_date": (date.today() - timedelta(days=1)).isoformat()},
              files={"file": ("a.xlsx", sample_sheet.build(), "application/octet-stream")})  # fmt: skip
    d = by_ref(db, "DM-000146")  # in the sheet as 8NTHV6
    d.status = "submitted"
    db.commit()
    team.post(f"/gtd-queue/{d.id}/link", data={"gtd_req_id": "NEW146"})
    later_sheet = sample_sheet.build(sample_sheet.without("8NTHV6"))
    team.post("/imports", data={"sheet_date": date.today().isoformat()},
              files={"file": ("b.xlsx", later_sheet, "application/octet-stream")})  # fmt: skip
    s = reconcile_service.latest_import(db, 1).summary  # type: ignore[union-attr]
    assert "DM-000146" not in s["dropped"] and "DM-000146" in s["awaiting"]


def test_more_time_keeps_the_level(db: Session) -> None:
    esc = open_esc(db, "DM-000121", "past_start")  # seeded overdue, at L2; Priya's demand
    assert esc is not None and esc.level == 2
    new = date.today() + timedelta(days=10)
    svc.resolve(db, actor(db, "priya"), esc.id, reason="Client budget pending", action="extend",
                comment="Budget sign-off next week", extend_to=new)  # fmt: skip
    esc = open_esc(db, "DM-000121", "past_start")
    assert esc is not None and esc.level == 2 and esc.status == "open"  # no drop back to L1
    assert esc.due_at.astimezone(svc._tz(db.get_one(Account, 1))).date() == new
    assert esc.events[-1].kind == "extended" and "Budget sign-off" in (esc.events[-1].note or "")
    mail.sent.clear()
    svc.sweep(db, 1, NOW)
    assert all("DM-000121" not in m.text for m in mail.sent)  # no reminders until the new date passes
    with pytest.raises(EscalationError, match="after today"):
        svc.resolve(db, actor(db, "priya"), esc.id, reason="Other", action="extend", comment="n/a",
                    extend_to=date.today())  # fmt: skip


def test_no_further_action_only_once_cleared(db: Session, client: Client) -> None:
    esc = open_esc(db, "DM-000149", "not_submitted")  # seeded
    assert esc is not None
    with pytest.raises(EscalationError, match="isn't available"):
        svc.resolve(db, actor(db, "farah"), esc.id, reason="Other", action="no_action", comment="n/a")
    client.as_user("farah").post(
        f"/gtd-queue/{by_ref(db, 'DM-000149').id}/link", data={"gtd_req_id": "LATE01"}
    )
    svc.resolve(
        db,
        actor(db, "farah"),
        esc.id,
        reason="Created on GTD now",
        action="no_action",
        comment="Linked a day late",
    )
    assert db.get_one(Escalation, esc.id).status == "resolved"


def test_only_the_responsible_party_responds(db: Session) -> None:
    gtd = open_esc(db, "DM-000139", "missing")  # the GTD admin team's to solve
    own = open_esc(db, "DM-000121", "past_start")  # the demand owner's (Priya)
    assert gtd is not None and own is not None
    for who in ("priya", "sanjay"):
        with pytest.raises(EscalationError, match="for the GTD admin team to respond"):
            svc.resolve(db, actor(db, who), gtd.id, reason="Other", action="close", comment="n/a")
    for who in ("farah", "kavya", "sanjay", "neha"):
        with pytest.raises(EscalationError, match="for the demand owner to respond"):
            svc.resolve(db, actor(db, who), own.id, reason="Other", action="close", comment="n/a")
    with pytest.raises(EscalationError, match="Choose a reason"):
        svc.resolve(db, actor(db, "farah"), gtd.id, reason="Because", action="close", comment=None)
    aging = open_esc(db, "DM-000135", "aging")
    assert aging is not None
    owner = actor_from_user(db, db.get_one(User, by_ref(db, "DM-000135").owner_id))
    with pytest.raises(EscalationError, match="isn't available"):
        svc.resolve(db, owner, aging.id, reason="Other", action="resubmit", comment="n/a")


# --- Screen ---------------------------------------------------------------------------------------


def test_escalations_screen_and_respond_over_http(client: Client, db: Session) -> None:
    client.as_user("farah")
    esc = open_esc(db, "DM-000139", "missing")
    assert esc is not None
    page = client.get(f"/escalations?id={esc.id}")
    assert page.status_code == 200 and "Missing from sheet" in page.text and "Medium severity" in page.text
    assert "Who acts:" in page.text and "Steps:" in page.text and ">Respond</button>" in page.text
    r = client.post(f"/escalations/{esc.id}/resolve", data={"reason": "Duplicate demand", "action": "close",
                                                           "comment": "Raised twice"})  # fmt: skip
    assert "msg=Escalation" in r.headers["location"]
    db.expire_all()
    assert db.get_one(Escalation, esc.id).status == "resolved"
    detail = client.get(r.headers["location"]).text
    assert "Duplicate demand" in detail and "Raised twice" in detail


def test_leadership_is_informed_and_never_the_one_to_act(client: Client, db: Session) -> None:
    client.as_user("sanjay")
    for ref, type_ in (("DM-000139", "missing"), ("DM-000121", "past_start")):
        esc = open_esc(db, ref, type_)
        assert esc is not None
        page = client.get(f"/escalations?id={esc.id}").text
        assert ">Respond</button>" not in page and "You are informed only" in page


def test_demand_owner_revises_the_start_date(db: Session) -> None:
    esc = open_esc(db, "DM-000121", "past_start")
    assert esc is not None
    with pytest.raises(EscalationError, match="revised start date"):
        svc.resolve(db, actor(db, "priya"), esc.id, reason="Other", action="new_start", comment="n/a")
    new = date.today() + timedelta(days=21)
    svc.resolve(db, actor(db, "priya"), esc.id, reason="Client budget pending", action="new_start",
                comment="Agreed with the client", extend_to=new)  # fmt: skip
    done = db.get_one(Escalation, esc.id)
    assert done.status == "resolved" and "Start date revised" in (done.comment or "")
    assert by_ref(db, "DM-000121").start_date == new
    svc.sweep(db, 1, NOW)
    assert open_esc(db, "DM-000121", "past_start") is None  # no longer late


def test_interviewers_have_no_escalations_screen(client: Client) -> None:
    assert client.as_user("vikram").get("/escalations").status_code == 403


def test_demand_owner_sees_only_their_own_escalations(client: Client, db: Session) -> None:
    page = client.as_user("priya").get("/escalations").text
    assert "My escalations" in page and "DM-000139" in page and "DM-000121" in page
    assert "DM-000133" not in page and "DM-000149" not in page  # other owners' demands
    theirs = open_esc(db, "DM-000133", "incorrect")
    assert theirs is not None
    assert "DM-000133" not in client.get(f"/escalations?id={theirs.id}").text
    mine = open_esc(db, "DM-000121", "past_start")
    assert mine is not None and ">Respond</button>" in client.get(f"/escalations?id={mine.id}").text


def test_owner_sees_escalations_on_their_demand(client: Client) -> None:
    page = client.as_user("priya").get("/demands/DM-000139").text
    assert "Escalations" in page and "Missing from sheet · L1" in page
    assert "/escalations?" not in page  # no link to a screen they can't open


def test_sweep_button_for_admin_roles_only(client: Client) -> None:
    assert "msg=Sweep" in client.as_user("farah").post("/escalations/sweep").headers["location"]
    assert "err=" in client.as_user("sanjay").post("/escalations/sweep").headers["location"]


# --- Rules: severity, on/off, who acts (Account settings) ----------------------------------------------


def test_severity_sets_the_time_to_respond(db: Session) -> None:
    resolve_all_seeded(db)
    account = db.get_one(Account, 1)
    d = by_ref(db, "DM-000146")
    tz = svc._tz(account)
    days = {}
    for type_ in (svc.EscalationType.DROPPED, svc.EscalationType.INCORRECT, svc.EscalationType.AGING):
        esc = svc.open_escalation(db, account, d, type_, "test", NOW)
        assert esc is not None
        days[esc.severity] = esc.due_at.astimezone(tz).date()
    from app.core.workdays import add_working_days

    today = NOW.astimezone(tz).date()
    assert days == {s: add_working_days(today, n) for s, n in (("high", 1), ("medium", 2), ("low", 3))}


def test_a_billable_demand_past_its_start_is_always_high(db: Session) -> None:
    account = db.get_one(Account, 1)
    cfg = account.settings
    cfg.escalation_rules["past_start"].severity = svc.Severity.LOW
    account.settings = cfg
    db.commit()
    resolve_all_seeded(db)
    svc.sweep(db, 1, NOW)
    esc = open_esc(db, "DM-000117", "past_start")
    assert esc is not None and by_ref(db, "DM-000117").position_type == "Billable" and esc.severity == "high"


def test_administrator_changes_the_rules_in_settings(client: Client, db: Session) -> None:
    resolve_all_seeded(db)
    account = db.get_one(Account, 1)
    form: dict[str, str] = {"days_high": "1", "days_medium": "4", "days_low": "6", "inform_leadership": "on"}
    for t in svc.EscalationType:
        r = account.settings.rule_for(t.value)
        form |= {f"enabled_{t.value}": "on", f"responsible_{t.value}": r.responsible.value,
                 f"severity_{t.value}": r.severity.value, f"steps_{t.value}": r.steps}  # fmt: skip
    del form["enabled_aging"]  # switch aging off
    form["responsible_incorrect"] = "gtd_team"  # the GTD team handles incorrect demands here
    form["steps_missing"] = "Ring GTD staffing and ask."
    assert client.as_user("kavya").post("/settings/escalation-rules", data=form).status_code == 403
    r = client.as_user("anil").post("/settings/escalation-rules", data=form)
    assert r.status_code == 303 and "err=" not in r.headers["location"]

    db.expire_all()
    account = db.get_one(Account, 1)
    cfg = account.settings
    assert not cfg.rule_for("aging").enabled and cfg.response_days == {"high": 1, "medium": 4, "low": 6}
    assert not cfg.l2_inform_delivery_head
    d = by_ref(db, "DM-000146")
    assert svc.open_escalation(db, account, d, svc.EscalationType.AGING, "quiet", NOW) is None  # off
    esc = svc.open_escalation(db, account, d, svc.EscalationType.INCORRECT, "wrong", NOW)
    assert esc is not None and esc.responsible == "gtd_team"
    db.commit()
    svc.resolve(db, actor(db, "farah"), esc.id, reason="Other", action="close", comment="n/a")

    page = client.get("/settings").text
    assert "Escalation rules" in page and "Ring GTD staffing and ask." in page
    bad = {**form, "days_high": "9"}
    assert "err=" in client.post("/settings/escalation-rules", data=bad).headers["location"]


def test_late_feedback_goes_to_the_interviewer_and_closes_when_given(db: Session) -> None:
    from app.models import Interview
    from app.services import interview_service
    from app.services.interview_service import Feedback

    resolve_all_seeded(db)
    account = db.get_one(Account, 1)
    iv = db.scalars(select(Interview).where(Interview.interviewer_id == user_id("vikram"))).first()
    assert iv is not None
    iv.scheduled_at = NOW - timedelta(hours=account.panel_timer_hours + 2)
    db.commit()
    svc.sweep(db, 1, NOW)
    d = db.get_one(Demand, iv.demand_id)
    esc = open_esc(db, d.app_ref, "panel_sla")
    assert esc is not None and esc.responsible == "interviewer" and esc.severity == "low"
    [m] = [m for m in mail.sent if m.to == ["vikram.p@example.com"]]
    assert "Steps: Submit your feedback" in m.text and "Who acts: Interviewer (Vikram P.)" in m.text

    from app.models import Candidate

    fb = Feedback(round=iv.round, ratings={"Technical depth": 4, "Problem solving": 4, "Communication": 4},
                  outcome="select", comments="ok")  # fmt: skip
    interview_service.record_feedback(
        db, account, user_id("vikram"), db.get_one(Candidate, iv.candidate_id), fb, iv
    )
    svc.sweep(db, 1, NOW)
    done = db.get_one(Escalation, esc.id)
    assert done.status == "resolved" and done.reason == "Feedback submitted" and done.resolved_by is None


def test_reasons_fit_the_trigger_and_the_demand_page_takes_the_response(client: Client, db: Session) -> None:
    esc = open_esc(db, "DM-000121", "past_start")  # Priya's demand
    assert esc is not None
    cfg = db.get_one(Account, 1).settings
    assert "Client moved the start date" in cfg.reasons_for("past_start")
    assert "Client moved the start date" not in cfg.reasons_for("missing")
    assert cfg.reasons_for("missing")[-1] == "Other"
    with pytest.raises(EscalationError, match="Choose a reason"):  # a reason for another trigger
        svc.resolve(db, actor(db, "priya"), esc.id, reason="Wrong requisition ID was linked",
                    action="close", comment=None)  # fmt: skip
    with pytest.raises(EscalationError, match="say what the reason is"):
        svc.resolve(db, actor(db, "priya"), esc.id, reason="Other", action="close", comment=None)

    page = client.as_user("priya").get("/demands/DM-000121").text  # the full demand, with the form on it
    assert "Needs your response" in page and "Client moved the start date" in page
    assert "Revise the start date" in page and "Commercial and timing" in page
    assert "Needs your response" not in client.as_user("farah").get("/demands/DM-000121").text
    assert "Open the full demand" in client.as_user("priya").get(f"/escalations?id={esc.id}").text
    new = date.today() + timedelta(days=14)
    r = client.as_user("priya").post(
        f"/escalations/{esc.id}/resolve",
        data={"reason": "Client moved the start date", "action": "new_start", "extend_to": new.isoformat(),
              "comment": "", "next": "/demands/DM-000121"},
    )  # fmt: skip
    assert r.headers["location"].startswith("/demands/DM-000121?msg=")
    assert by_ref(db, "DM-000121").start_date == new and db.get_one(Escalation, esc.id).status == "resolved"


def test_administrator_sets_the_reasons_for_a_trigger(client: Client, db: Session) -> None:
    account = db.get_one(Account, 1)
    form = {"days_high": "1", "days_medium": "2", "days_low": "3"}
    for t in svc.EscalationType:
        r = account.settings.rule_for(t.value)
        form |= {f"enabled_{t.value}": "on", f"responsible_{t.value}": r.responsible.value,
                 f"severity_{t.value}": r.severity.value, f"steps_{t.value}": r.steps}  # fmt: skip
    form["reasons_aging"] = "Hiring freeze\nRole on hold\n"
    assert client.as_user("anil").post("/settings/escalation-rules", data=form).status_code == 303
    db.expire_all()
    cfg = db.get_one(Account, 1).settings
    assert cfg.reasons_for("aging") == ["Hiring freeze", "Role on hold", "Other"]
    assert "Offer in progress" in cfg.reasons_for("past_start")  # untouched triggers keep the defaults
