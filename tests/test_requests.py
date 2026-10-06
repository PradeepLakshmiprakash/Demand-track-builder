"""Raise a request: anyone asks the Administrator by picking from options; the Administrator does it."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.enums import EscalationType, Role, Severity
from app.models import Account, AdminRequest, BusinessUnit, Demand, UserAccount
from app.services import request_service
from tests.conftest import Client, user_id

GRACE = {"kind": "settings", "st_what": "grace", "st_value": "5"}
GRACE_TEXT = "Account settings: Change the grace period (working days): 5."


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def last(db: Session) -> AdminRequest:
    db.expire_all()
    req = db.scalars(select(AdminRequest).order_by(AdminRequest.id.desc())).first()
    assert req is not None
    return req


def acct(db: Session) -> int:
    """The account the seeded people work in."""
    aid = db.scalar(select(UserAccount.account_id).where(UserAccount.user_id == user_id("kavya")))
    assert aid is not None
    return aid


def cfg(db: Session):  # type: ignore[no-untyped-def]
    return db.get_one(Account, acct(db)).settings


def rate_form(db: Session) -> dict[str, str]:
    c = cfg(db)
    return {
        "kind": "rate_card",
        "rc_practice": c.practices[0],
        "rc_grade": c.grades[0],
        "rc_cost": "73",
        "rc_from": "2026-11-01",
    }


def other_bu(db: Session, have: set[str]) -> str:
    names = db.scalars(select(BusinessUnit.name).where(BusinessUnit.account_id == acct(db)))
    return next(n for n in sorted(names) if n not in have)


def test_lead_admin_asks_and_the_administrator_is_mailed(client: Client, db: Session) -> None:
    form = rate_form(db)
    r = client.as_user("kavya").post("/requests", data=form)
    assert r.status_code == 303 and "sent" in r.headers["location"]
    text = last(db).details
    assert text.startswith(f"Rate card: {form['rc_grade']} in {form['rc_practice']} to $73.00 an hour")
    assert "from 01 Nov 2026." in text
    [m] = mail.sent
    assert m.to == ["anil.v@example.com"] and m.cc == ["kavya.r@example.com"]
    assert "Request #1: Rate card" in m.subject and text in m.text and "Steps:" in m.text
    assert "to $73.00 an hour" in client.as_user("anil").get("/requests").text


def test_administrator_marks_it_done_and_the_requester_is_told(client: Client) -> None:
    client.as_user("kavya").post("/requests", data=GRACE)
    mail.sent.clear()
    r = client.as_user("anil").post("/requests/1", data={"status": "done", "note": "Added today"})
    assert r.status_code == 303
    [m] = mail.sent
    assert m.to == ["kavya.r@example.com"] and "Request #1 done" in m.subject and "Added today" in m.text
    assert "Done by Anil V." in client.as_user("kavya").get("/requests").text
    # handled once only
    assert "err=" in client.as_user("anil").post("/requests/1", data={"status": "done"}).headers["location"]


def test_decline_needs_a_reason(client: Client) -> None:
    client.as_user("kavya").post("/requests", data=GRACE)
    r = client.as_user("anil").post("/requests/1", data={"status": "declined", "note": ""})
    assert "err=" in r.headers["location"]


def test_only_the_right_roles(client: Client) -> None:
    assert client.as_user("anil").post("/requests", data=GRACE).status_code == 403  # raises nothing himself
    client.as_user("kavya").post("/requests", data=GRACE)
    assert client.post("/requests/1", data={"status": "done"}).status_code == 403  # Kavya can't mark her own
    # another account's Administrator can't see or handle it
    assert "grace period" not in client.as_user("rosa").get("/requests").text
    assert "err=" in client.post("/requests/1", data={"status": "done"}).headers["location"]


def test_the_form_is_choices_and_shows_what_you_have(client: Client, db: Session) -> None:
    c = client.as_user("priya")
    page = c.get("/requests").text
    form = page.split('id="req-form"')[1].split("</form>")[0]
    assert "Your current access" in page and "Demand owner" in page
    assert "<textarea" not in form and 'name="details"' not in form  # nothing free-flowing to fill in
    assert 'name="role" value="demand_owner" checked' in form
    for bu in db.scalars(select(BusinessUnit.name).where(BusinessUnit.account_id == acct(db))):
        assert f'name="bu" value="{bu}"' in form
    # only what the role can ask about
    assert 'value="data_fix"' in form and 'value="rate_card"' not in form and 'name="rc_cost"' not in form
    lead = client.as_user("kavya").get("/requests").text
    assert 'name="special"' in lead and "Which escalation" in lead and "Raise a request" in lead
    assert "Lead admin" in lead  # how the role reads when Kavya is signed in


def test_access_request_is_the_difference_from_today(client: Client, db: Session) -> None:
    c = client.as_user("priya")
    page = c.get("/requests").text
    have = {b for b in db.scalars(select(BusinessUnit.name)) if f'name="bu" value="{b}" checked' in page}
    same = {"kind": "access", "bu": sorted(have), "role": "demand_owner"}
    assert "err=" in c.post("/requests", data=same).headers["location"]  # nothing changed, nothing sent
    assert mail.sent == []
    extra = other_bu(db, have)
    c.post("/requests", data=same | {"bu": [*sorted(have), extra], "role": "leadership"})
    assert last(db).details == (
        f"Access for Priya N.: Business units: add {extra}. Role: change from Demand owner to Leadership."
    )
    if have:
        c.post("/requests", data=same | {"bu": [extra]})
        assert f"Business units: add {extra}; remove {', '.join(sorted(have))}." in last(db).details


def test_every_role_can_ask_for_what_it_needs(client: Client, db: Session) -> None:
    rule = cfg(db).rule_for(EscalationType.MISSING.value)
    to = next(s for s in Severity if s is not rule.severity)
    mine = db.scalars(
        select(Demand).where(Demand.owner_id == user_id("priya"), Demand.status != "draft")
    ).first()
    assert mine is not None
    asks = [
        ("priya", {"kind": "data_fix", "df_demand": mine.app_ref, "df_field": "grade", "df_value": "C1"},
         f"Data correction on {mine.app_ref} ({mine.name}): Grade should be C1."),
        ("farah", {"kind": "settings", "st_what": "map_status", "st_value": "Paused", "st_stage": "coverage"},
         "Map a new BCM sheet status to a stage: Paused → Resourcing In Progress."),
        ("sanjay", {"kind": "escalation", "es_trigger": "missing", "es_severity": to.value},
         f"Escalation rule '{EscalationType.MISSING.label}': severity {rule.severity.label} → {to.label}."),
    ]  # fmt: skip
    for who, form, expect in asks:
        mail.sent.clear()
        c = client.as_user(who)
        r = c.post("/requests", data=form)
        assert r.status_code == 303 and "sent" in r.headers["location"], r.headers["location"]
        assert expect in last(db).details
        [m] = mail.sent
        assert m.to == ["anil.v@example.com"] and expect in m.text
        shown = expect.replace("'", "&#39;")
        assert shown in c.get("/requests").text  # their own request
        assert shown in client.as_user("anil").get("/requests").text  # the Administrator sees it
        assert shown in client.as_user("kavya").get("/requests").text  # so does the Lead admin


def test_choices_are_checked(client: Client) -> None:
    c = client.as_user("kavya")
    for bad in (
        {"kind": "settings", "st_what": "grace", "st_value": "soon"},
        {"kind": "settings", "st_what": "map_status", "st_value": "On Hold"},  # no stage chosen
        {"kind": "rate_card", "rc_practice": "Nowhere", "rc_grade": "Z9", "rc_cost": "70"},
        {"kind": "escalation", "es_trigger": "missing"},  # nothing to change
        {"kind": "data_fix", "df_demand": "DM-999999", "df_field": "grade", "df_value": "C1"},
        {"kind": "special"},
        {"kind": "other"},
    ):  # fmt: skip
        loc = c.post("/requests", data=bad).headers["location"]
        assert "err=" in loc and loc.endswith("#new"), bad
    assert mail.sent == []


def test_interviewer_asks_for_a_profile_change(client: Client, db: Session) -> None:
    c = client.as_user("vikram")
    mine = next(p for u, p in request_service.interviewers(db, acct(db)) if u.id == user_id("vikram"))
    skills = list(mine.skills or [])
    c.post("/requests", data={"kind": "profile", "pf_skill": [*skills, "Kafka-Streams-X"]})
    text = last(db).details
    assert text.startswith("Interviewer profile of Vikram") and "Skills: add Kafka-Streams-X." in text
    # nothing changed is not a request; and the rate card isn't his to ask about
    assert "err=" in c.post("/requests", data={"kind": "profile", "pf_skill": skills}).headers["location"]
    assert 'value="rate_card"' not in c.get("/requests").text
    assert "err=" in c.post("/requests", data=rate_form(db)).headers["location"]


def test_people_see_only_their_own_requests(client: Client) -> None:
    client.as_user("priya").post("/requests", data=GRACE)
    assert GRACE_TEXT in client.get("/requests").text
    page = client.as_user("neha").get("/requests").text  # another demand owner
    assert GRACE_TEXT not in page and "You have not asked" in page


def test_lead_admin_special_access_is_on_record(client: Client, db: Session) -> None:
    first, second = request_service.SPECIAL[Role.ADMIN][:2]
    c = client.as_user("kavya")
    r = c.post("/requests", data={"kind": "special", "special": [first, second]})
    assert "%231%2C%20%232" in r.headers["location"]  # one request for each access asked for
    assert "In place" not in c.get("/requests").text  # asked, not yet given
    client.as_user("anil").post("/requests/1", data={"status": "done", "note": "Granted"})
    page = client.as_user("kavya").get("/requests").text
    assert "In place" in page and "granted" in page
    # what is in place can't be asked for again
    again = client.as_user("kavya").post("/requests", data={"kind": "special", "special": [first]})
    assert "err=" in again.headers["location"]
