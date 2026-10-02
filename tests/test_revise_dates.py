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
    assert "Change the dates" in page and "Last working day (leaver)" in page
    assert ">Demand owner<" in page and "Priya N." in page
    assert "Change the dates" not in client.as_user("farah").get(f"/demands/{REF}").text

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
