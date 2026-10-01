from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services import chart_service, loss_service
from tests.conftest import Client


def test_donut_slices_cover_the_whole_ring() -> None:
    c = chart_service.donut("T", "h", [("A", 3, "3"), ("B", 0, "0"), ("C", 1, "1")], "4", "things")
    assert [s.pct for s in c.slices] == [75, 0, 25] and [s.label for s in c.drawn] == ["A", "C"]
    assert c.slices[2].color == chart_service.SLOTS[2]  # colour follows the position, not the rank
    assert abs(-c.drawn[1].offset - 0.75 * chart_service.CIRCUMFERENCE) < 0.01


def test_donut_never_has_more_than_five_slices() -> None:
    c = chart_service.donut("T", "h", [(str(i), Decimal(1), "1") for i in range(8)], "8", "things")
    assert len(c.slices) == 5 and c.slices[-1].label == "Other" and c.slices[-1].display == "4 more"


def test_donut_with_nothing_draws_nothing() -> None:
    assert chart_service.donut("T", "h", [("A", 0, "0")], "0", "things").drawn == []


def test_overview_shows_the_three_charts(client: Client, db: Session) -> None:
    page = client.as_user("sanjay").get("/overview").text
    for title in ("Positions by stage", "Revenue lost by business unit", "Open positions by type"):
        assert title in page
    o = loss_service.overview(db, 1, date.today())
    assert sum(o.mix.values()) == o.open and sum(n for _, _, n in o.pipeline) == o.live
