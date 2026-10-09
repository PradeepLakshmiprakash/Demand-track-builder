"""From offer to first billable day: the pre-joining checklist, a candidate who does not join, the first
billable day, the settings behind them, and the speed report."""

import json
import re
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.enums import DemandStatus, EscalationType
from app.core.workdays import add_working_days
from app.models import Account, CandidateExit, Demand, Escalation, OnboardingItem, StageEvent
from app.services import (
    escalation_service,
    loss_service,
    onboarding_service,
    speed_service,
    workflow_service,
)
from tests.conftest import Client, user_id

OFFER_MADE = "DM-000117"  # Priya's, offer made, joining awaited
JOINED = "DM-000116"  # Priya's, joined


@pytest.fixture(autouse=True)
def clear_outbox() -> None:
    mail.sent.clear()


def demand(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def items(db: Session, ref: str) -> list[OnboardingItem]:
    return onboarding_service.items(db, demand(db, ref).id)


def open_of(db: Session, ref: str, kind: EscalationType) -> list[Escalation]:
    d = demand(db, ref)
    stmt = select(Escalation).where(
        Escalation.demand_id == d.id, Escalation.type == kind.value, Escalation.status == "open"
    )
    return list(db.scalars(stmt))


def test_checklist_starts_when_the_offer_is_made(client: Client, db: Session) -> None:
    d = demand(db, OFFER_MADE)
    d.expected_doj = add_working_days(date.today(), 30)
    db.commit()
    page = client.as_user("priya").get(f"/demands/{OFFER_MADE}").text
    assert "Pre-joining checklist" in page and "0 of 8 done" in page
    rows = items(db, OFFER_MADE)
    assert [i.key for i in rows][:2] == ["offer_accepted", "bgv_started"] and len(rows) == 8
    # people in the app by name; a party outside it as named, with no person
    assert "<strong>Priya N.</strong>" in page and "GTD staffing" in page and "outside the app" in page
    assert "GTD admin team" in page and "the owner records it" in page
    # a demand without an offer has none
    assert "Pre-joining checklist" not in client.get("/demands/DM-000142").text


def test_the_demand_owner_updates_every_item(client: Client, db: Session) -> None:
    client.as_user("priya").get(f"/demands/{OFFER_MADE}")
    by_key = {i.key: i for i in items(db, OFFER_MADE)}
    own, team, outside = by_key["offer_accepted"], by_key["laptop"], by_key["bgv_started"]
    url = f"/demands/{OFFER_MADE}/checklist/"
    c = client.as_user("priya")
    assert "updated" in c.post(url + str(own.id), data={"status": "done"}).headers["location"]
    assert "err=" in c.post(url + str(team.id), data={"status": "blocked"}).headers["location"]  # no note
    c.post(url + str(team.id), data={"status": "blocked", "note": "Stock due Friday"})
    c.post(url + str(outside.id), data={"status": "in_progress", "note": "Vendor started"})
    # nobody else updates it: not the GTD admin team, not its lead
    for other in ("farah", "kavya"):
        r = client.as_user(other).post(url + str(own.id), data={"status": "not_started"})
        assert "err=" in r.headers["location"]
    by_key = {i.key: i for i in items(db, OFFER_MADE)}
    assert by_key["offer_accepted"].status == "done" and by_key["laptop"].status == "blocked"
    assert by_key["laptop"].owner_id is None and by_key["bgv_started"].owner_id is None  # no person named
    page = client.as_user("priya").get(f"/demands/{OFFER_MADE}").text
    assert "1 of 8 done" in page and "1 blocked" in page and "Stock due Friday" in page
    assert page.count('action="/demands/' + OFFER_MADE + "/checklist/") == 8  # she can update all eight
    assert (
        "checklist/"
        not in client.as_user("kavya").get(f"/demands/{OFFER_MADE}").text.split("Pre-joining")[1][:6000]
    )


def test_an_overdue_item_escalates_and_clears(client: Client, db: Session) -> None:
    d = demand(db, OFFER_MADE)
    d.expected_doj = add_working_days(date.today(), 1)  # joining tomorrow: most items are already due
    db.commit()
    now = datetime.now(UTC)
    escalation_service.sweep(db, 1, now)
    [esc] = open_of(db, OFFER_MADE, EscalationType.PREJOIN_OVERDUE)
    assert "Offer accepted in writing: due" in esc.detail and "more)" in esc.detail
    assert esc.responsible == "demand_owner"  # the first overdue item is the owner's
    account = db.get_one(Account, 1)
    assert not escalation_service.is_cleared(db, account, esc, demand(db, OFFER_MADE), now)
    page = client.as_user("priya").get(f"/demands/{OFFER_MADE}").text
    assert "working day" in page and "late" in page and "overdue" in page
    for i in items(db, OFFER_MADE):
        i.status = "done"
    db.commit()
    assert escalation_service.is_cleared(db, account, esc, demand(db, OFFER_MADE), now)
    # the due date follows the joining date
    rows = items(db, OFFER_MADE)  # read first: reading again would discard the unsaved date below
    d = db.get_one(Demand, rows[0].demand_id)
    d.expected_doj = add_working_days(date.today(), 40)
    for i in rows:
        i.status = "not_started"
    db.commit()
    assert demand(db, OFFER_MADE).id not in onboarding_service.overdue_items(db, account, date.today())


def test_a_candidate_who_does_not_join_sends_the_demand_back(client: Client, db: Session) -> None:
    c = client.as_user("priya")
    c.get(f"/demands/{OFFER_MADE}")  # its checklist starts
    form = {"kind": "declined", "reason": "Took another offer", "on_date": date.today().isoformat()}
    bad = c.post(f"/demands/{OFFER_MADE}/no-join", data=form | {"reason": "Because"})
    assert "err=" in bad.headers["location"]
    assert "err=" in client.as_user("neha").post(f"/demands/{OFFER_MADE}/no-join", data=form).headers.get(
        "location", "err="
    )  # another owner can't even see it
    r = client.as_user("priya").post(f"/demands/{OFFER_MADE}/no-join", data=form)
    assert r.status_code == 303 and "Sourcing" in r.headers["location"]
    d = demand(db, OFFER_MADE)
    assert d.status_enum is DemandStatus.COVERAGE_REQUIRED and d.expected_doj is None
    [x] = db.scalars(select(CandidateExit).where(CandidateExit.demand_id == d.id)).all()
    assert (x.kind, x.reason, x.recorded_by) == ("declined", "Took another offer", user_id("priya"))
    assert {i.status for i in items(db, OFFER_MADE)} == {"not_needed"}
    [m] = mail.sent
    assert "will not join: back to sourcing" in m.subject and "kavya.r@example.com" in m.to
    page = client.as_user("priya").get(f"/demands/{OFFER_MADE}").text
    assert "Candidates who did not join" in page and "Declined the offer" in page
    assert "The candidate will not join" not in page  # no offer is out any more


def awaiting(db: Session, days_ago: int) -> Demand:
    """The joined sample demand, as it is on the day billing still has to be confirmed."""
    d = demand(db, JOINED)
    d.billable_from, d.expected_doj = None, add_working_days(date.today(), -days_ago)
    db.commit()
    return demand(db, JOINED)


def test_joined_stays_in_client_onboarding_until_billing_is_confirmed(client: Client, db: Session) -> None:
    d = awaiting(db, 10)
    account = db.get_one(Account, 1)
    assert d.awaits_billing
    assert workflow_service.position(d) == {
        "stage": "Client Onboarding In Progress",
        "sub": "Joined, billing to be confirmed",
    }
    assert workflow_service.layout()["stages"][2]["subs"][-1] == "Joined, billing to be confirmed"
    # still open, and still losing revenue: joining doesn't stop the loss, billing does
    o = loss_service.overview(db, 1, date.today())
    assert d.id in {x.demand.id for x in o.at_risk} and not o.loss_of[d.id].filled
    page = client.as_user("priya").get(f"/demands/{JOINED}").text
    assert "Has client billing started?" in page and "Not confirmed" in page
    assert "Yes, from the joining day" in page and '"sub": "Joined, billing to be confirmed"' in page
    rows = client.as_user("priya").get("/demands?filter=alloc_pending").text
    assert JOINED in rows or "43TUIX" in rows  # listed under client onboarding, not under completed
    # the owner is asked once, on or after the joining day
    mail.sent.clear()
    now = datetime.now(UTC)
    escalation_service.sweep(db, 1, now)
    asks = [m for m in mail.sent if "confirm the first billable day" in m.subject]
    assert len(asks) == 1 and asks[0].to == ["priya.n@example.com"] and "kavya.r@example.com" in asks[0].cc
    escalation_service.sweep(db, 1, now)
    assert len([m for m in mail.sent if "confirm the first billable day" in m.subject]) == 1
    [esc] = open_of(db, JOINED, EscalationType.NOT_BILLING)
    assert "10 working days without client billing" in esc.detail
    assert account.settings.onboarding.not_billing_after_days == 5


def test_the_owner_confirms_the_first_billable_day(client: Client, db: Session) -> None:
    d = awaiting(db, 10)
    account = db.get_one(Account, 1)
    now = datetime.now(UTC)
    escalation_service.sweep(db, 1, now)
    [esc] = open_of(db, JOINED, EscalationType.NOT_BILLING)
    c = client.as_user("priya")
    url = f"/demands/{JOINED}/billing"
    early = (d.expected_doj - timedelta(days=3)).isoformat()
    assert "err=" in c.post(url, data={"billable_from": early}).headers["location"]  # before joining
    assert "err=" in c.post(url, data={"waiting": "1", "reason": "Because"}).headers["location"]
    for other in ("farah", "kavya"):  # the owner's to confirm, nobody else's
        r = client.as_user(other).post(url, data={"billable_from": d.expected_doj.isoformat()})
        assert "err=" in r.headers["location"]
    c = client.as_user("priya")
    # a reason answers the escalation, but the seat still counts as open
    c.post(url, data={"waiting": "1", "reason": "Purchase order not issued"})
    d = demand(db, JOINED)
    assert d.not_billing_reason == "Purchase order not issued" and d.awaits_billing
    assert escalation_service.is_cleared(db, account, esc, d, now)
    # billing started two days ago: eight working days were lost between joining and billing
    started = add_working_days(date.today(), -2)
    ok = c.post(url, data={"billable_from": started.isoformat()})
    assert "Billing" in ok.headers["location"] and "err=" not in ok.headers["location"]
    d = demand(db, JOINED)
    assert d.billable_from == started and d.not_billing_reason is None and not d.awaits_billing
    assert workflow_service.position(d) == {"stage": "Allocation Completed", "sub": "Joined"}
    b = onboarding_service.billing(db, account, [d], date.today())[d.id]
    assert not b.waiting and b.waiting_days == 8
    loss = {x.demand.id: x for x in loss_service.losses(db, 1, date.today())}[d.id]
    assert loss.filled  # the loss stopped on the first billable day
    # confirming the joining day itself is one click, and leaves no gap
    c.post(url, data={"billable_from": d.expected_doj.isoformat()})
    d = demand(db, JOINED)
    assert onboarding_service.billing(db, account, [d], date.today())[d.id].waiting_days == 0


def test_overview_counts_the_wait_for_billing(client: Client, db: Session) -> None:
    awaiting(db, 4)
    page = client.as_user("sanjay").get("/overview").text
    assert '"nobill": "Joined, not billing"' in page and '"nobill_days": 4' in page
    data = json.loads(re.search(r"window\.OV = (\{.*?\});</script>", page, re.S).group(1))  # type: ignore[union-attr]
    row = next(r for r in data["rows"] if r["ref"] == JOINED)
    assert row["stage"] == "Client Onboarding In Progress" and row["open"] is True
    assert row["sub"] == "Joined, billing to be confirmed"


def test_administrator_sets_the_onboarding_rules(client: Client, db: Session) -> None:
    c = client.as_user("anil")
    page = c.get("/settings").text
    assert "Onboarding: from offer to first billable day" in page and "Offer accepted in writing" in page
    assert "Pre-joining item overdue" in page and "Joined, not billing" in page  # under Escalation rules
    base = {
        "exit_reasons": "Took another offer\nRelocated",
        "not_billing_reasons": "Waiting for access",
        "billing_step": "1",
        "not_billing_after_days": "7",
        "target_time_to_fill": "35",
        "target_offer_in_market": "",
    }
    rows = {
        "key": ["offer_accepted", "", ""],
        "label": ["Offer accepted in writing", "Client badge ordered", "Induction booked"],
        "owner_kind": ["owner", "outside", "person"],
        "person_id": ["", "", str(user_id("deepak"))],
        "outside_label": ["", "Client", ""],
        "due_days_before": ["12", "5", "3"],
        "enabled": ["0", "1", "2"],
        "escalate": ["0", "2"],
    }
    r = c.post("/settings/onboarding", data=base | rows)
    assert "msg=Saved" in r.headers["location"], r.headers["location"]
    db.expire_all()
    ob = db.get_one(Account, 1).settings.onboarding
    assert [(i.label, i.owner_kind, i.escalate) for i in ob.checklist] == [
        ("Offer accepted in writing", "owner", True),
        ("Client badge ordered", "outside", False),
        ("Induction booked", "person", True),
    ]
    assert ob.checklist[1].outside_label == "Client" and ob.checklist[2].person_id == user_id("deepak")
    assert ob.exit_reasons == ["Took another offer", "Relocated"] and ob.not_billing_after_days == 7
    assert ob.targets == {"time_to_fill": 35}
    # an outside party needs naming; a named person needs choosing
    bad = rows | {"outside_label": ["", "", ""]}
    assert "err=" in c.post("/settings/onboarding", data=base | bad).headers["location"]
    bad = rows | {"person_id": ["", "", ""]}
    assert "err=" in c.post("/settings/onboarding", data=base | bad).headers["location"]
    assert client.as_user("kavya").post("/settings/onboarding", data=base | rows).status_code == 403
    # a new offer's checklist follows the new list
    client.as_user("priya").get(f"/demands/{OFFER_MADE}")
    assert [i.label for i in items(db, OFFER_MADE)] == [i.label for i in ob.checklist]


def test_speed_report(client: Client, db: Session) -> None:
    d = demand(db, JOINED)
    db.execute(StageEvent.__table__.delete().where(StageEvent.demand_id == d.id))
    day = date.today() - timedelta(days=50)
    trail = [("submitted", 0), ("sent_to_gtd", 1), ("coverage_required", 6), ("offer_in_process", 26),
             ("offer_in_market", 28), ("staffed", 40)]  # fmt: skip
    before = None
    for to, offset in trail:
        at = datetime.combine(day + timedelta(days=offset), datetime.min.time(), UTC)
        db.add(StageEvent(demand_id=d.id, from_stage=before, to_stage=to, origin="app", at=at))
        before = to
    d.submitted_at = datetime.combine(day, datetime.min.time(), UTC)
    d.expected_doj = day + timedelta(days=40)
    db.commit()
    account = db.get_one(Account, 1)
    r = speed_service.report(db, account, date.today(), days=90)
    assert r.filled == 1 and r.time_to_fill is not None and 26 <= r.time_to_fill <= 30
    sourcing = next(s for s in r.steps if s.key == "coverage_required")
    assert sourcing.count == 1 and sourcing.days is not None and sourcing.over  # 14 working days against 10
    assert [s.demand.app_ref for s in r.stays["coverage_required"]] == [JOINED]
    assert r.groups[0].name == "PAYMENTS" and r.groups[0].filled == 1
    c = client.as_user("sanjay")
    page = c.get("/speed?step=coverage_required").text
    assert "Speed report" in page and "Where the time goes" in page and "slowest first" in page
    assert "By demand owner" in c.get("/speed?by=owner").text
    csv = c.get("/speed.csv")
    assert csv.status_code == 200 and "Time to fill" in csv.text and JOINED in csv.text
    assert client.as_user("priya").get("/speed").status_code == 403
    assert client.as_user("kavya").get("/speed").status_code == 200
