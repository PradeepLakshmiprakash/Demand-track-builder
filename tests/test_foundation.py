"""Schema and core rules: migrations match the models, Postgres owns app refs, list descriptions."""

from datetime import date

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import Base, get_engine
from app.core.security import actor_from_user
from app.models import Demand, User
from app.services.demand_service import demand_rows, filter_counts, summary
from tests.conftest import bu_id, user_id


def test_migrations_match_models() -> None:
    with get_engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == []


def test_postgres_assigns_app_ref(db: Session) -> None:
    d = Demand(
        account_id=1,
        bu_id=bu_id("DATA"),
        owner_id=user_id("arjun"),
        name="New",
        practice="DMN-FS",
        grade="C1",
        status="draft",
    )
    db.add(d)
    db.commit()
    assert d.app_ref == "DM-000152"  # continues after the highest seeded ref
    assert d.gtd_name == "New"  # the plain name goes to GTD: no DM reference to type


def test_rows_and_summary_for_priya(db: Session) -> None:
    actor = actor_from_user(db, db.get_one(User, user_id("priya")))
    rows = demand_rows(db, actor, today=date(2026, 9, 23))
    by_ref = {r.demand.app_ref: r for r in rows}

    assert by_ref["DM-000139"].status_label == "Missing · escalated L1"
    assert by_ref["DM-000121"].status_label == "Past start · escalated L2"
    assert by_ref["DM-000117"].chip == "risk"  # past start, no escalation yet
    assert by_ref["DM-000117"].note.startswith("22 days past start date")
    assert not by_ref["DM-000116"].attention  # staffed is never "past start"
    assert by_ref["DM-000142"].req_id == "2ZT7KP"

    assert summary(rows) == {"open": 7, "coverage": 4, "selection": 0, "alloc_pending": 3, "attention": 4}
    assert filter_counts(rows) == {
        "archived": 0,
        "all": 8,
        "attention": 4,
        "coverage": 4,
        "selection": 0,
        "alloc_pending": 3,
        "alloc_done": 1,
        "abandoned": 0,
    }
    assert by_ref["DM-000142"].main.label == "Coverage Required"  # main stage; the sub-stage is the label


def test_seed_has_two_accounts(db: Session) -> None:
    from app.models import Account

    assert [a.name for a in db.scalars(select(Account).order_by(Account.id))] == [
        "Discover NA",
        "Acme Insurance",
    ]
