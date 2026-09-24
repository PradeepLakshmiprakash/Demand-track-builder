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
    assert d.gtd_name == "[DM-000152] New"


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

    assert summary(rows) == {"open": 7, "before_gtd": 2, "in_coverage": 1, "attention": 4}
    assert filter_counts(rows) == {"all": 8, "attention": 4, "before_gtd": 2, "linked": 5, "finished": 1}


def test_seed_is_single_account(db: Session) -> None:
    from app.models import Account

    assert [a.name for a in db.scalars(select(Account))] == ["Discover NA"]
