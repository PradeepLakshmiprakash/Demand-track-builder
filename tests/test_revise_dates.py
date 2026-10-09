"""Start date and last working day stay editable after the demand is on GTD."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.models import Demand
from tests.conftest import Client

REF = "DM-000121"  # Priya's, on GTD, past its start


def demand(db: Session, ref: str = REF) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def test_owner_changes_the_dates_after_gtd(client: Client, db: Session) -> None:
    d = demand(db)
    d.type, d.replaced_resource, d.lwd = "Replacement", "Someone Leaving", date.today()
    db.commit()
    page = client.as_user("priya").get(f"/demands/{REF}").text
    # dates are changed in place now, with the pencil on the box, like everything else on the demand
    assert "Change the dates" not in page and 'data-edit="commercial"' in page
    assert "Last working day (leaver)" in page and ">Demand owner<" in page and "Priya N." in page
    assert "data-edit=" not in client.as_user("farah").get(f"/demands/{REF}").text

    mail.sent.clear()
    start, lwd = date.today() + timedelta(days=20), date.today() + timedelta(days=5)
    r = client.as_user("priya").post(
        f"/demands/{REF}/dates", data={"start_date": start.isoformat(), "lwd": lwd.isoformat()}
    )
    assert "msg=Dates" in r.headers["location"]
    d = demand(db)
    assert (d.start_date, d.lwd) == (start, lwd)
    [m] = [m for m in mail.sent if "Dates changed" in m.subject]
    assert "Start date:" in m.text and "Last working day:" in m.text and "kavya.r@example.com" in m.to

    r = client.as_user("priya").post(f"/demands/{REF}/dates", data={"start_date": start.isoformat()})
    assert "err=" in r.headers["location"]  # a replacement needs its last working day
    r = client.as_user("farah").post(
        f"/demands/{REF}/dates", data={"start_date": date.today().isoformat(), "lwd": lwd.isoformat()}
    )
    assert "err=" in r.headers["location"] and demand(db).start_date == start


def test_not_on_a_finished_demand(client: Client, db: Session) -> None:
    owner_page = client.as_user("priya").get("/demands/DM-000116")  # joined
    if owner_page.status_code == 200:
        assert "Change the dates" not in owner_page.text
    assert "Change the dates" not in client.as_user("kavya").get("/demands/DM-000116").text


def test_extending_the_due_date_quietens_the_form(client: Client, db: Session) -> None:
    from app.models import Escalation

    esc = db.scalars(
        select(Escalation).where(
            Escalation.demand_id == demand(db).id,
            Escalation.status == "open",
            Escalation.type == "past_start",
        )
    ).one()
    page = client.as_user("priya").get(f"/demands/{REF}").text
    assert "Needs your response" in page and "You asked for more time" not in page
    later = date.today() + timedelta(days=10)
    client.as_user("priya").post(
        f"/escalations/{esc.id}/resolve",
        data={"reason": "Offer in progress", "action": "extend", "extend_to": later.isoformat(),
              "comment": "", "next": f"/demands/{REF}"},
    )  # fmt: skip
    page = client.as_user("priya").get(f"/demands/{REF}").text
    assert "You asked for more time, until" in page and "Respond again or close it now" in page
    assert "Needs your response" not in page and "Escalation · more time given" in page
    assert "More time was given" in client.as_user("priya").get(f"/escalations?id={esc.id}").text


def test_moving_the_start_date_ahead_closes_the_past_start_escalation(client: Client, db: Session) -> None:
    from app.models import Escalation

    d = demand(db)
    start = date.today() + timedelta(days=20)
    r = client.as_user("priya").post(f"/demands/{REF}/dates", data={"start_date": start.isoformat()})
    assert (
        "escalation+is+closed" in r.headers["location"] or "escalation%20is%20closed" in r.headers["location"]
    )
    db.expire_all()
    esc = db.scalars(
        select(Escalation).where(Escalation.demand_id == d.id, Escalation.type == "past_start")
    ).one()
    assert (esc.status, esc.action, esc.reason) == ("resolved", "new_start", "Other")
    assert "Needs your response · L2" not in client.as_user("priya").get(f"/demands/{REF}").text
