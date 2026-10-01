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
    esc = open_esc(db, "DM-000117", "past_start")  # started 1 Sep, offer in market, no DP sheet yet
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


def test_l1_mail_goes_to_bu_delivery_head_with_owner_and_admins_copied(db: Session) -> None:
    resolve_all_seeded(db)
    svc.sweep(db, 1, NOW)
    to_ritu = [m for m in mail.sent if m.to == ["ritu.s@example.com"]]  # PAYMENTS delivery head
    assert len(to_ritu) == 1
    m = to_ritu[0]
    assert "L1 LOB delivery head" in m.subject and "DM-000117" in m.text
    assert "priya.n@example.com" in m.cc and "kavya.r@example.com" in m.cc and "farah.q@example.com" in m.cc
    assert "sanjay.m@example.com" not in m.cc


def test_bu_without_delivery_head_falls_back_to_admin(db: Session) -> None:
    resolve_all_seeded(db)
    from app.models import BusinessUnit

    db.execute(update(BusinessUnit).values(delivery_head_email=None, delivery_head_name=None))
    db.commit()
    svc.sweep(db, 1, NOW)
    assert all(m.to == ["kavya.r@example.com"] for m in mail.sent)


def test_overdue_l1_is_promoted_to_l2_and_mailed_to_leadership(db: Session) -> None:
    resolve_all_seeded(db)
    svc.sweep(db, 1, NOW)
    esc = open_esc(db, "DM-000117", "past_start")
    assert esc is not None and esc.level == 1
    mail.sent.clear()
    later = esc.due_at + timedelta(minutes=1)
    result = svc.sweep(db, 1, later)
    esc = open_esc(db, "DM-000117", "past_start")
    assert esc is not None and esc.level == 2 and esc.notified_level == 2
    assert esc.due_at > later and any("DM-000117" in p for p in result.promoted)
    assert [e.kind for e in esc.events] == ["opened", "notified", "promoted", "notified"]
    l2 = [m for m in mail.sent if m.to == ["sanjay.m@example.com"]]
    assert len(l2) == 1 and "L2 Account leadership" in l2[0].subject
    assert "ritu.s@example.com" in l2[0].cc and "priya.n@example.com" in l2[0].cc
    # L2 past due stays L2 (there is no L3).
    svc.sweep(db, 1, esc.due_at + timedelta(days=1))
    assert open_esc(db, "DM-000117", "past_start").level == 2  # type: ignore[union-attr]


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
        actor(db, "farah"),
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
        user_id("farah"),
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


def test_extend_returns_to_l1_with_new_date(db: Session) -> None:
    esc = open_esc(db, "DM-000121", "past_start")  # seeded at L2
    assert esc is not None and esc.level == 2
    new = date.today() + timedelta(days=10)
    svc.resolve(db, actor(db, "sanjay"), esc.id, reason="Client budget pending", action="extend",
                comment="Budget sign-off next week", extend_to=new)  # fmt: skip
    esc = open_esc(db, "DM-000121", "past_start")
    assert esc is not None and esc.level == 1 and esc.status == "open"
    assert esc.due_at.astimezone(svc._tz(db.get_one(Account, 1))).date() == new
    assert esc.events[-1].kind == "extended" and "Budget sign-off" in (esc.events[-1].note or "")
    with pytest.raises(EscalationError, match="after today"):
        svc.resolve(db, actor(db, "farah"), esc.id, reason="Unknown", action="extend", comment=None,
                    extend_to=date.today())  # fmt: skip


def test_no_further_action_only_once_cleared(db: Session, client: Client) -> None:
    esc = open_esc(db, "DM-000149", "not_submitted")  # seeded
    assert esc is not None
    with pytest.raises(EscalationError, match="isn't available"):
        svc.resolve(db, actor(db, "farah"), esc.id, reason="Unknown", action="no_action", comment=None)
    client.as_user("farah").post(
        f"/gtd-queue/{by_ref(db, 'DM-000149').id}/link", data={"gtd_req_id": "LATE01"}
    )
    svc.resolve(
        db, actor(db, "farah"), esc.id, reason="Unknown", action="no_action", comment="Linked a day late"
    )
    assert db.get_one(Escalation, esc.id).status == "resolved"


def test_who_may_resolve_each_level(db: Session) -> None:
    l1 = open_esc(db, "DM-000139", "missing")
    l2 = open_esc(db, "DM-000121", "past_start")
    assert l1 is not None and l2 is not None
    with pytest.raises(EscalationError, match="resolved by leadership or the GTD team admin"):
        svc.resolve(db, actor(db, "farah"), l2.id, reason="Unknown", action="close", comment=None)
    with pytest.raises(EscalationError, match="GTD team admin or GTD admin team"):
        svc.resolve(db, actor(db, "sanjay"), l1.id, reason="Unknown", action="close", comment=None)
    with pytest.raises(EscalationError, match="Choose a reason"):
        svc.resolve(db, actor(db, "farah"), l1.id, reason="Because", action="close", comment=None)
    with pytest.raises(EscalationError, match="isn't available"):
        aging = open_esc(db, "DM-000135", "aging")
        assert aging is not None
        svc.resolve(db, actor(db, "farah"), aging.id, reason="Unknown", action="resubmit", comment=None)


# --- Screen ---------------------------------------------------------------------------------------


def test_escalations_screen_and_resolve_over_http(client: Client, db: Session) -> None:
    client.as_user("farah")
    page = client.get("/escalations")
    assert page.status_code == 200 and "Missing from sheet" in page.text and "Resolve escalation" in page.text
    esc = open_esc(db, "DM-000139", "missing")
    assert esc is not None
    r = client.post(f"/escalations/{esc.id}/resolve", data={"reason": "Duplicate demand", "action": "close",
                                                           "comment": "Raised twice"})  # fmt: skip
    assert "msg=Escalation" in r.headers["location"]
    db.expire_all()
    assert db.get_one(Escalation, esc.id).status == "resolved"
    detail = client.get(r.headers["location"]).text
    assert "Duplicate demand" in detail and "Raised twice" in detail


def test_leadership_sees_l2_resolve_form_only(client: Client, db: Session) -> None:
    client.as_user("sanjay")
    l1 = open_esc(db, "DM-000139", "missing")
    l2 = open_esc(db, "DM-000121", "past_start")
    assert l1 is not None and l2 is not None
    assert "Resolve escalation" not in client.get(f"/escalations?id={l1.id}").text
    assert "Resolve escalation" in client.get(f"/escalations?id={l2.id}").text


def test_admin_demand_owner_can_resolve_l2(db: Session) -> None:
    l2 = open_esc(db, "DM-000121", "past_start")
    assert l2 is not None and l2.level == 2
    svc.resolve(db, actor(db, "kavya"), l2.id, reason="Client budget pending", action="close", comment=None)
    assert db.get_one(Escalation, l2.id).status == "resolved"


@pytest.mark.parametrize("who", ["priya", "vikram"])
def test_only_admin_roles_and_leadership_see_escalations(client: Client, who: str) -> None:
    assert client.as_user(who).get("/escalations").status_code == 403


def test_owner_sees_escalations_on_their_demand(client: Client) -> None:
    page = client.as_user("priya").get("/demands/DM-000139").text
    assert "Escalations" in page and "Missing from sheet · L1" in page
    assert "/escalations?" not in page  # no link to a screen they can't open


def test_sweep_button_for_admin_roles_only(client: Client) -> None:
    assert "msg=Sweep" in client.as_user("farah").post("/escalations/sweep").headers["location"]
    assert "err=" in client.as_user("sanjay").post("/escalations/sweep").headers["location"]
