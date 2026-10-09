"""Keeps the practice and grade tables (models/reference.py) in step with everything that uses them.

Two rules, applied to every database session so no screen, job or script can skip them:

- before a demand or a rate is written, its practice and grade exist for its account (added as inactive
  if they aren't on the account's list, e.g. a name that came from a BCM sheet);
- after an account's settings are written, its lists are mirrored: names on the list are active and in
  list order, names taken off the list stay but become inactive.

Renaming is reference_service.rename(): one update there, and the foreign keys carry it to every demand
and rate.
"""

from typing import Any

from sqlalchemy import event, inspect, text
from sqlalchemy.orm import Session

from app.models import Account, Demand, RateCard

ENSURE = {
    "practices": text(
        "INSERT INTO practices (account_id, name, active, position) VALUES (:a, :n, false, 999) "
        "ON CONFLICT (account_id, name) DO NOTHING"
    ),
    "grades": text(
        "INSERT INTO grades (account_id, name, active, position) VALUES (:a, :n, false, 999) "
        "ON CONFLICT (account_id, name) DO NOTHING"
    ),
}
MIRROR = {
    t: text(
        f"INSERT INTO {t} (account_id, name, active, position) VALUES (:a, :n, true, :p) "
        "ON CONFLICT (account_id, name) DO UPDATE SET active = true, position = EXCLUDED.position"
    )
    for t in ("practices", "grades")
}
RETIRE = {
    t: text(f"UPDATE {t} SET active = false WHERE account_id = :a AND active AND NOT (name = ANY(:keep))")
    for t in ("practices", "grades")
}


def _ensure(session: Session, _context: Any, _instances: Any) -> None:
    seen: set[tuple[str, int, str]] = set()
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Demand | RateCard) or obj.account_id is None:
            continue
        for table, name in (("practices", obj.practice), ("grades", obj.grade)):
            if name and (table, obj.account_id, name) not in seen:
                seen.add((table, obj.account_id, name))
                session.execute(ENSURE[table], {"a": obj.account_id, "n": name})


def _mirror(session: Session, _context: Any) -> None:
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Account):
            continue
        if obj not in session.new and not inspect(obj).attrs.config.history.has_changes():
            continue
        cfg = obj.settings
        for table, names in (("practices", cfg.practices), ("grades", cfg.grades)):
            for n, name in enumerate(names):
                session.execute(MIRROR[table], {"a": obj.id, "n": name, "p": n})
            session.execute(RETIRE[table], {"a": obj.id, "keep": list(names)})


def install() -> None:
    if not event.contains(Session, "before_flush", _ensure):
        event.listen(Session, "before_flush", _ensure)
        event.listen(Session, "after_flush", _mirror)


install()
