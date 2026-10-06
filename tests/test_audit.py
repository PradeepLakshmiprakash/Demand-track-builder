"""The audit trail, the same-site rule for form posts, and the guard against a partial sheet."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.models import AuditEntry, Demand
from app.services import import_service
from seed import sample_sheet
from tests.conftest import Client


def entries(db: Session, entity: str) -> list[AuditEntry]:
    db.expire_all()
    stmt = select(AuditEntry).where(AuditEntry.entity == entity, AuditEntry.action == "changed")
    return list(db.scalars(stmt.order_by(AuditEntry.id)))  # the seed's own "created" rows aside


def test_a_change_is_recorded_with_who_and_before_and_after(db: Session) -> None:
    d = db.scalars(select(Demand).where(Demand.app_ref == "DM-000121")).one()
    audit.set_actor(db, d.owner_id, "Priya N.", d.account_id)
    before = d.client_rate
    d.client_rate = before + 5
    d.location = "Riverwoods"
    db.commit()
    [e] = [x for x in entries(db, "demand") if x.entity_id == d.id]
    assert e.action == "changed" and e.actor_name == "Priya N." and e.entity_label.startswith("DM-000121")
    assert e.changes["client_rate"] == [str(before), str(before + 5)]
    assert e.changes["location"][1] == "Riverwoods" and "updated_at" not in e.changes


def test_nothing_is_recorded_when_nothing_changed_or_it_is_rolled_back(db: Session) -> None:
    d = db.scalars(select(Demand).where(Demand.app_ref == "DM-000121")).one()
    d.location = d.location  # no change
    db.commit()
    d.location = "Elsewhere"
    db.flush()
    db.rollback()
    assert [x for x in entries(db, "demand") if x.entity_id == d.id] == []


def test_settings_changes_name_the_part_that_changed(client: Client, db: Session) -> None:
    c = client.as_user("anil")
    page = c.get("/settings").text
    assert "Account settings" in page
    from app.models import Account

    acc = db.get_one(Account, 1)
    audit.set_actor(db, None, "Anil V.", 1)
    cfg = dict(acc.config)
    cfg["practices"] = [*cfg["practices"], "NEW-FS"]
    acc.config = cfg
    acc.grace_days = acc.grace_days + 1
    db.commit()
    [e] = entries(db, "account")
    assert set(e.changes) == {"config.practices", "grace_days"} and e.actor_name == "Anil V."


def test_change_history_screen(client: Client, db: Session) -> None:
    d = db.scalars(select(Demand).where(Demand.app_ref == "DM-000121")).one()
    audit.set_actor(db, None, "Kavya R.", 1)
    d.hiring_manager = "A. Client"
    db.commit()
    page = client.as_user("anil").get("/audit?entity=demand&q=DM-000121").text
    assert (
        "Change history" in page and "hiring manager" in page and "A. Client" in page and "Kavya R." in page
    )
    assert client.as_user("kavya").get("/audit").status_code == 200
    assert client.as_user("priya").get("/audit").status_code == 403  # not a demand owner's screen
    assert "Change history" in client.as_user("anil").get("/users").text  # in the menu


def test_a_post_from_another_site_is_refused(client: Client) -> None:
    c = client.as_user("kavya")
    form = {"kind": "settings", "st_what": "grace", "st_value": "5"}
    assert c.post("/requests", data=form, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/requests", data=form, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    ok = c.post(
        "/requests", data=form, headers={"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    )
    assert ok.status_code == 303


def test_a_much_shorter_sheet_is_questioned(client: Client) -> None:
    c = client.as_user("kavya")
    full = sample_sheet.build()
    assert c.post("/imports", files={"file": ("BCM_1-Jan-2099.xlsx", full)}).status_code == 303
    few = sample_sheet.build(sample_sheet.ROWS[:4])
    assert len(sample_sheet.ROWS) >= import_service.SHRINK_MIN_ROWS
    r = c.post("/imports", files={"file": ("BCM_2-Jan-2099.xlsx", few)})
    assert r.status_code == 400 and "the last one had" in r.text and "Import this sheet anyway" in r.text
    again = c.post("/imports", files={"file": ("BCM_2-Jan-2099.xlsx", few)}, data={"confirm_older": "1"})
    assert again.status_code == 303
