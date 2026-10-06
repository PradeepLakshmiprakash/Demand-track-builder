"""Writes the audit trail (models/audit.py) for every tracked change, whichever screen or job made it.

It listens to the database session rather than being called from each service: a new way of changing a
demand can't forget to record it. Who made the change comes from the session (`db.info`), which the
sign-in step fills in; changes made by a job or a script are recorded as "System".
"""

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.models import Account, BusinessUnit, Demand, InterviewerProfile, RateCard, User, UserAccount
from app.models.audit import AuditEntry

# Record type → (name in the trail, fields left out because they change by themselves).
TRACKED: dict[type, tuple[str, frozenset[str]]] = {
    Demand: ("demand", frozenset({"updated_at", "created_at", "interviewers_alerted_at", "id"})),
    User: ("user", frozenset({"id", "created_at"})),
    UserAccount: ("access", frozenset({"id", "created_at"})),
    Account: ("account", frozenset({"id", "created_at"})),
    BusinessUnit: ("business unit", frozenset({"id"})),
    RateCard: ("rate", frozenset({"id", "created_at"})),
    InterviewerProfile: ("interviewer profile", frozenset({"id"})),
}
MAX = 300


def set_actor(db: Session, actor_id: int | None, name: str, account_id: int | None) -> None:
    """Called at sign-in resolution: changes made through this session are recorded under this person."""
    db.info["audit_actor"] = (actor_id, name, account_id)


def _plain(v: Any) -> Any:
    if v is None or isinstance(v, bool | int | float):
        return v
    if isinstance(v, Enum):
        return v.value
    if isinstance(v, Decimal | date | datetime):
        return str(v)
    if isinstance(v, list | tuple | set):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        text = json.dumps(v, sort_keys=True, default=str)
        return text if len(text) <= MAX else text[:MAX] + "…"
    text = str(v)
    return text if len(text) <= MAX else text[:MAX] + "…"


def _label(obj: Any) -> str:
    if isinstance(obj, Demand):
        return f"{obj.app_ref or 'new demand'} · {obj.name}"[:200]
    if isinstance(obj, UserAccount):
        return f"membership of user {obj.user_id}"
    if isinstance(obj, RateCard):
        return f"{obj.grade} in {obj.practice or 'any practice'}"
    if isinstance(obj, InterviewerProfile):
        return f"profile of user {obj.user_id}"
    return str(getattr(obj, "name", None) or getattr(obj, "id", ""))[:200]


def _account_of(obj: Any, fallback: int | None) -> int | None:
    if isinstance(obj, Account):
        return obj.id
    return getattr(obj, "account_id", None) or fallback


def _changes(obj: Any, skip: frozenset[str], created: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    state = inspect(obj)
    for col in state.mapper.column_attrs:
        key = col.key
        if key in skip:
            continue
        hist = state.attrs[key].history
        if created:
            value = getattr(obj, key)
            if value not in (None, [], {}, ""):
                out[key] = [None, _plain(value)]
            continue
        if not hist.has_changes():
            continue
        before = hist.deleted[0] if hist.deleted else None
        after = hist.added[0] if hist.added else None
        if isinstance(before, dict) and isinstance(after, dict):  # settings: say which part changed
            for part in sorted(set(before) | set(after)):
                if before.get(part) != after.get(part):
                    out[f"{key}.{part}"] = [_plain(before.get(part)), _plain(after.get(part))]
        elif _plain(before) != _plain(after):
            out[key] = [_plain(before), _plain(after)]
    return out


def _record(session: Session, _flush_context: Any) -> None:
    actor_id, name, account_id = session.info.get("audit_actor", (None, "System", None))
    entries: list[AuditEntry] = []
    for objs, action in ((session.new, "created"), (session.dirty, "changed"), (session.deleted, "deleted")):
        for obj in objs:
            kind = TRACKED.get(type(obj))
            if kind is None:
                continue
            entity, skip = kind
            changes = {} if action == "deleted" else _changes(obj, skip, action == "created")
            if action == "changed" and not changes:
                continue
            entries.append(
                AuditEntry(
                    account_id=_account_of(obj, account_id),
                    actor_id=actor_id,
                    actor_name=name,
                    entity=entity,
                    entity_id=getattr(obj, "id", None),
                    entity_label=_label(obj),
                    action=action,
                    changes=changes,
                )
            )
    session.add_all(entries)  # written by the flush that follows, inside the same transaction


def install() -> None:
    """Start recording. Safe to call more than once."""
    if not event.contains(Session, "after_flush", _record):
        event.listen(Session, "after_flush", _record)


install()
