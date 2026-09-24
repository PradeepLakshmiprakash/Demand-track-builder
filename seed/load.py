"""Reset the database contents and load the dummy data in seed/data.py."""

import secrets
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.db import Base
from app.models import (
    Account,
    BusinessUnit,
    Candidate,
    Demand,
    Escalation,
    EscalationEvent,
    GtdSubmission,
    Interview,
    InterviewerProfile,
    StageEvent,
    User,
    UserPractice,
)
from seed import data


def reset(db: Session) -> None:
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    db.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    db.execute(text("ALTER SEQUENCE demand_ref_seq RESTART WITH 1"))


def load(db: Session, now: datetime | None = None) -> None:
    now = now or datetime.now(UTC)
    reset(db)

    account = Account(**data.ACCOUNT, mail_time=time(9, 0), config=data.CONFIG, active=True)
    db.add(account)
    db.flush()
    bus = {
        name: BusinessUnit(
            account_id=account.id,
            name=name,
            active=True,
            delivery_head_name=data.DELIVERY_HEADS[name][0],
            delivery_head_email=data.DELIVERY_HEADS[name][1],
        )
        for name in data.BUSINESS_UNITS
    }
    db.add_all(bus.values())
    db.flush()

    users: dict[str, User] = {}
    for key, name, email, role, level, scope, bu_names, practices, iv in data.USERS:
        u = User(name=name, email=email, role=role, level=level, visibility_scope=scope, active=True)
        u.accounts = [account]
        u.business_units = [bus[b] for b in bu_names]
        u.practice_links = [UserPractice(practice=p) for p in practices]
        if iv:
            u.interviewer_profile = InterviewerProfile(
                practices=practices, skills=iv[0], max_grade=iv[1], active=True
            )
        users[key] = u
    db.add_all(users.values())
    db.flush()
    admin = users["kavya"]

    demands: dict[str, Demand] = {}
    for ref, owner, bu, name, practice, grade, start, status, req, extra in data.DEMANDS:
        fields: dict[str, Any] = {**data.DEFAULTS, **extra}  # type: ignore[dict-item]
        fields["client_rate"] = Decimal(str(fields["client_rate"]))
        d = Demand(
            app_ref=ref,
            account_id=account.id,
            bu_id=bus[bu].id,
            owner_id=users[owner].id,
            name=name,
            practice=practice,
            grade=grade,
            start_date=start,
            status=status,
            submitted_at=None if status == "draft" else now - timedelta(days=10),
            **fields,
        )
        db.add(d)
        db.flush()
        demands[ref] = d
        db.add(
            StageEvent(
                demand_id=d.id, from_stage=None, to_stage=status, origin="app", actor_id=users[owner].id
            )
        )
        if req:
            db.add(
                GtdSubmission(
                    demand_id=d.id,
                    gtd_req_id=req,
                    submitted_by=admin.id,
                    submitted_at=now - timedelta(days=9),
                )
            )

    for ref, etype, esc_level, due_in, detail in data.ESCALATIONS:
        due = now + timedelta(days=due_in)
        opened = due - timedelta(days=account.l1_sla_days + (account.l2_sla_days if esc_level == 2 else 0))
        esc = Escalation(
            demand_id=demands[ref].id, type=etype, level=esc_level, status="open", detail=detail,
            opened_at=opened, due_at=due, notified_level=esc_level,  # seeded as already mailed
        )  # fmt: skip
        db.add(esc)
        db.flush()
        db.add(EscalationEvent(escalation_id=esc.id, kind="opened", level=1, at=opened, note=detail))
        if esc_level == 2:
            db.add(EscalationEvent(escalation_id=esc.id, kind="promoted", level=2, at=due - timedelta(days=3),
                                   note="L1 due date passed"))  # fmt: skip

    for ref, cand, rnd, iv_key, when in data.INTERVIEWS:
        d = demands[ref]
        c = Candidate(demand_id=d.id, name=cand, channel="fte", current_stage="internal_panel")
        db.add(c)
        db.flush()
        db.add(
            Interview(
                demand_id=d.id,
                candidate_id=c.id,
                round=rnd,
                interviewer_id=users[iv_key].id,
                scheduled_at=datetime(*when, tzinfo=UTC),
                feedback_token=secrets.token_urlsafe(32),
            )
        )

    # Postgres owns app refs: continue the sequence after the seeded ones.
    db.execute(
        text("SELECT setval('demand_ref_seq', (SELECT max(substring(app_ref from 4)::int) FROM demands))")
    )
    db.commit()
