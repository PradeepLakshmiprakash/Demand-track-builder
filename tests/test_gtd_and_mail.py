"""Flow 2: daily admin mail and GTD submission. Includes the Phase 2 exit check."""

import csv
import io
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.workdays import add_working_days
from app.models import Account, Demand, GtdSubmission, NotificationBatch, StageEvent
from app.services import notify_service
from tests.conftest import Client
from tests.test_raise_demand import form, ref_from

CHICAGO = ZoneInfo("America/Chicago")


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def by_ref(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def account(db: Session) -> Account:
    return db.scalars(select(Account).where(Account.name == "Discover NA")).one()


# --- Daily admin mail ------------------------------------------------------------------------------


def test_mail_lists_submitted_and_marks_them_notified(db: Session) -> None:
    result = notify_service.send_daily_admin_mail(db, account(db).id, force=True)
    assert result.batch is not None
    # Seed: DM-000151 (PAYMENTS) and DM-000147 (DATA) are submitted; DM-000149 is already notified.
    assert by_ref(db, "DM-000151").status == "notified"
    assert by_ref(db, "DM-000147").status == "notified"
    batch = db.scalars(select(NotificationBatch)).one()
    assert set(batch.demand_ids) == {by_ref(db, "DM-000151").id, by_ref(db, "DM-000147").id}

    [m] = mail.sent
    assert set(m.to) == {"kavya.r@example.com", "farah.q@example.com", "deepak.l@example.com"}
    assert "2 to enter on GTD" in m.subject
    assert "Senior Java Full Stack Developer" in m.text and "[DM-000151]" not in m.text
    assert "Still without a requisition ID (1)" in m.text and "DM-000149" in m.text


def test_mail_skips_leadership_owners_and_inactive_admin_team(client: Client, db: Session) -> None:
    from tests.conftest import user_id

    client.as_user("anil").post(f"/users/{user_id('deepak')}/active", data={"active": "0"})
    notify_service.send_daily_admin_mail(db, account(db).id, force=True)
    assert set(mail.sent[0].to) == {"kavya.r@example.com", "farah.q@example.com"}


def test_mail_goes_once_a_day(db: Session) -> None:
    acc = account(db)
    now = datetime(2026, 9, 24, 10, 0, tzinfo=CHICAGO)
    assert notify_service.send_daily_admin_mail(db, acc.id, now=now).batch is not None
    again = notify_service.send_daily_admin_mail(db, acc.id, now=now.replace(hour=15))
    assert again.batch is None and "already" in again.message
    assert len(mail.sent) == 1


def test_is_due_follows_account_mail_time_and_zone(db: Session) -> None:
    acc = account(db)
    acc.mail_time = time(9, 0)
    db.commit()
    # 13:59 UTC is 08:59 in Chicago (CDT): not yet. 14:01 UTC is 09:01: due.
    assert not notify_service.is_due(db, acc, datetime(2026, 9, 24, 13, 59, tzinfo=UTC))
    assert notify_service.is_due(db, acc, datetime(2026, 9, 24, 14, 1, tzinfo=UTC))
    notify_service.send_daily_admin_mail(db, acc.id, now=datetime(2026, 9, 24, 14, 1, tzinfo=UTC))
    assert not notify_service.is_due(db, acc, datetime(2026, 9, 24, 20, 0, tzinfo=UTC))
    assert notify_service.is_due(db, acc, datetime(2026, 9, 25, 14, 1, tzinfo=UTC))


def test_nothing_to_send(db: Session) -> None:
    for d in db.scalars(select(Demand).where(Demand.status.in_(["submitted", "notified"]))):
        d.status = "draft"
    db.commit()
    result = notify_service.send_daily_admin_mail(db, account(db).id, force=True)
    assert result.batch is None and "Nothing to send" in result.message
    assert mail.sent == []


def test_failed_send_leaves_demands_submitted(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_: mail.Mail) -> None:
        raise ConnectionError("SMTP down")

    monkeypatch.setattr(mail, "send", boom)
    with pytest.raises(ConnectionError):
        notify_service.send_daily_admin_mail(db, account(db).id, force=True)
    assert by_ref(db, "DM-000151").status == "submitted"
    assert db.scalar(select(NotificationBatch)) is None


def test_mail_cutoff_skips_weekend() -> None:
    from datetime import date

    assert add_working_days(date(2026, 9, 25), 1) == date(2026, 9, 28)  # Friday → Monday


def test_console_backend_writes_eml(db: Session) -> None:
    from app.core.config import get_settings

    folder = get_settings().local_path(get_settings().mail_dir)
    before = set(folder.glob("*.eml")) if folder.exists() else set()
    notify_service.send_daily_admin_mail(db, account(db).id, force=True)
    new = set(folder.glob("*.eml")) - before
    assert len(new) == 1 and b"Subject: [Demand Tracker]" in next(iter(new)).read_bytes()


# --- GTD queue -------------------------------------------------------------------------------------


@pytest.fixture
def team(client: Client) -> Client:
    return client.as_user("farah")  # GTD admin team does the manual GTD work


def test_queue_shows_mail_and_new_submissions(team: Client) -> None:
    q = team.get("/api/gtd-queue").json()
    assert [d["app_ref"] for d in q["in_mail"]] == ["DM-000149"]
    assert {d["app_ref"] for d in q["next_mail"]} == {"DM-000151", "DM-000147"}
    assert q["in_mail"][0]["gtd_name"] == "Cards Mainframe Developer · Chicago"


def test_link_requisition(team: Client, db: Session) -> None:
    d = by_ref(db, "DM-000149")
    r = team.post(f"/gtd-queue/{d.id}/link", data={"gtd_req_id": " k7tr2q "})
    assert "msg=K7TR2Q" in r.headers["location"]
    d = by_ref(db, "DM-000149")
    assert d.status == "sent_to_gtd" and d.gtd_req_id == "K7TR2Q"
    sub = db.scalars(select(GtdSubmission).where(GtdSubmission.gtd_req_id == "K7TR2Q")).one()
    from tests.conftest import user_id

    assert sub.submitted_by == user_id("farah")
    last = db.scalars(
        select(StageEvent).where(StageEvent.demand_id == d.id).order_by(StageEvent.id.desc())
    ).first()
    assert last is not None and (last.from_stage, last.to_stage) == ("notified", "sent_to_gtd")


@pytest.mark.parametrize(
    ("value", "message"),
    [("2ZT7KP", "already linked to DM-000142"), ("AB", "look like"), ("ZZ-99!", "look like")],
)
def test_link_rejects_bad_or_reused_ids(team: Client, db: Session, value: str, message: str) -> None:
    d = by_ref(db, "DM-000149")
    r = team.post(f"/gtd-queue/{d.id}/link", data={"gtd_req_id": value})
    assert "err=" in r.headers["location"]
    page = team.get(r.headers["location"])
    assert message in page.text.replace("&#39;", "'")
    assert by_ref(db, "DM-000149").status == "notified"


@pytest.mark.parametrize(("ref", "status"), [("DM-000150", "draft"), ("DM-000142", "coverage_required")])
def test_cannot_link_a_draft_or_linked_demand(team: Client, db: Session, ref: str, status: str) -> None:
    r = team.post(f"/gtd-queue/{by_ref(db, ref).id}/link", data={"gtd_req_id": "NEW123"})
    assert "err=" in r.headers["location"] and by_ref(db, ref).status == status


def test_only_admin_roles_use_the_queue(client: Client, db: Session) -> None:
    d = by_ref(db, "DM-000149")
    for who in ("priya", "sanjay", "vikram"):
        client.as_user(who)
        assert client.get("/gtd-queue").status_code == 403
        assert client.post(f"/gtd-queue/{d.id}/link", data={"gtd_req_id": "HACK01"}).status_code == 403
    assert client.as_user("kavya").get("/gtd-queue").status_code == 200


def test_export_has_the_plain_gtd_name(team: Client) -> None:
    r = team.get("/gtd-queue/export.csv")
    assert r.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(r.text)))
    names = {row["Demand request name"] for row in rows}
    assert "Senior Java Full Stack Developer (Java + Spring Boot + AWS)" in names
    assert len(rows) == 3


def test_send_mail_now_button(team: Client, db: Session) -> None:
    r = team.post("/gtd-queue/send-mail")
    assert "msg=Admin" in r.headers["location"]
    assert by_ref(db, "DM-000151").status == "notified"


# --- Phase 2 exit ----------------------------------------------------------------------------------


def test_phase2_exit_draft_to_sent_to_gtd(client: Client, db: Session) -> None:
    """A demand goes from draft to sent_to_gtd with a linked requisition ID; the admin receives the mail."""
    client.as_user("arjun")
    ref = ref_from(client.post("/demands/new", data=form(action="draft", practice="DMN-FS", grade="C2")))
    client.post(f"/demands/{ref}/submit")
    assert by_ref(db, ref).status == "submitted"

    notify_service.send_daily_admin_mail(db, account(db).id, force=True)
    assert by_ref(db, ref).status == "notified"
    assert any("kavya.r@example.com" in m.to and ref in m.text for m in mail.sent)

    client.as_user("deepak").post(f"/gtd-queue/{by_ref(db, ref).id}/link", data={"gtd_req_id": "Q4WN8Z"})
    d = by_ref(db, ref)
    assert (d.status, d.gtd_req_id) == ("sent_to_gtd", "Q4WN8Z")
    history = [
        e.to_stage
        for e in db.scalars(select(StageEvent).where(StageEvent.demand_id == d.id).order_by(StageEvent.id))
    ]
    assert history == ["draft", "submitted", "notified", "sent_to_gtd"]

    page = client.as_user("arjun").get(f"/demands/{ref}").text
    assert "Q4WN8Z" in page and "Sent to GTD" in page
