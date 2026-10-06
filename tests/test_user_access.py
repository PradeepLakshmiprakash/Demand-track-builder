"""User access page: add, edit, deactivate, and the role-locked visibility rules."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User, UserAccount
from tests.conftest import Client, bu_id, user_id


def _form(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "Test User",
        "email": "test.user@example.com",
        "role": "demand_owner",
        "level": "C1",
        "scope": "own",
        "bu_ids": [str(bu_id("DATA"))],
    }
    base.update(kw)
    return base


def _get(db: Session, email: str) -> User:
    return db.scalars(select(User).where(User.email == email)).one()


def _m(db: Session, email: str) -> UserAccount:
    """Their membership of Discover NA, where role and visibility live."""
    return db.scalars(
        select(UserAccount).join(User).where(User.email == email, UserAccount.account_id == 1)
    ).one()


@pytest.fixture
def admin(client: Client) -> Client:
    return client.as_user("anil")


def test_add_demand_owner(admin: Client, db: Session) -> None:
    r = admin.post("/users", data=_form())
    assert r.status_code == 303
    u = _get(db, "test.user@example.com")
    m = _m(db, "test.user@example.com")
    assert (m.role, m.visibility_scope) == ("demand_owner", "own")
    assert [b.name for b in u.business_units] == ["DATA"]
    assert u.created_by == user_id("anil")
    assert [a.name for a in u.accounts] == ["Discover NA"]


def test_leadership_is_forced_to_full_account(admin: Client, db: Session) -> None:
    admin.post("/users", data=_form(role="leadership", scope="own"))
    u = _get(db, "test.user@example.com")
    assert _m(db, "test.user@example.com").visibility_scope == "full"
    assert {b.name for b in u.business_units} == {"CARDS", "BANKING", "PAYMENTS", "DATA"}


def test_demand_owner_cannot_be_given_full_account(admin: Client, db: Session) -> None:
    admin.post("/users", data=_form(scope="full"))
    assert _m(db, "test.user@example.com").visibility_scope == "own"


def test_add_interviewer_with_profile(admin: Client, db: Session) -> None:
    admin.post(
        "/users",
        data=_form(
            role="interviewer", scope="own", practices=["CCA-FS"], skills="Java, AWS, Java", max_grade="C2"
        ),
    )
    u = _get(db, "test.user@example.com")
    assert _m(db, "test.user@example.com").visibility_scope == "assigned_interviews"
    assert u.interviewer_profile is not None
    assert u.interviewer_profile.skills == ["Java", "AWS"]
    assert u.interviewer_profile.max_grade == "C2"
    assert u.practices == ["CCA-FS"]


@pytest.mark.parametrize("bus", [[], ["DATA", "CARDS"]])
def test_demand_owner_has_exactly_one_bu(admin: Client, bus: list[str]) -> None:
    r = admin.post("/users", data=_form(bu_ids=[str(bu_id(b)) for b in bus]))
    assert r.status_code == 400
    assert "exactly one business unit" in r.text


def test_interviewer_may_cover_several_bus(admin: Client, db: Session) -> None:
    admin.post("/users", data=_form(role="interviewer", bu_ids=[str(bu_id("DATA")), str(bu_id("CARDS"))]))
    assert {b.name for b in _get(db, "test.user@example.com").business_units} == {"DATA", "CARDS"}


def test_admin_team_is_forced_to_full_account(admin: Client, db: Session) -> None:
    admin.post("/users", data=_form(role="admin_team", scope="own"))
    u = _get(db, "test.user@example.com")
    assert _m(db, "test.user@example.com").visibility_scope == "full"
    assert {b.name for b in u.business_units} == {"CARDS", "BANKING", "PAYMENTS", "DATA"}


def test_email_is_unique_ignoring_case(admin: Client) -> None:
    r = admin.post("/users", data=_form(email="PRIYA.N@example.com"))
    assert r.status_code == 400
    assert "already a user" in r.text


def test_invalid_email_rejected(admin: Client) -> None:
    assert admin.post("/users", data=_form(email="not-an-email")).status_code == 400


def test_deactivate_removes_access_immediately(admin: Client, client: Client) -> None:
    priya = user_id("priya")
    assert admin.post(f"/users/{priya}/active", data={"active": "0"}).status_code == 303
    client.as_user("priya")
    r = client.get("/demands")
    assert r.status_code == 403 and "access has been removed" in r.text
    assert client.get("/api/demands").status_code == 403
    client.as_user("anil").post(f"/users/{priya}/active", data={"active": "1"})
    assert client.as_user("priya").get("/api/demands").status_code == 200


def test_deactivated_user_keeps_demands(admin: Client, db: Session) -> None:
    admin.post(f"/users/{user_id('priya')}/active", data={"active": "0"})
    seen = admin.as_user("kavya").get("/api/demands").json()
    assert sum(d["owner"] == "Priya N." for d in seen) == 8


def test_cannot_deactivate_yourself(admin: Client, db: Session) -> None:
    r = admin.post(f"/users/{user_id('kavya')}/active", data={"active": "0"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert _m(db, "kavya.r@example.com").active


def test_last_admin_cannot_be_demoted(admin: Client, db: Session) -> None:
    kavya = user_id("kavya")
    r = admin.post(
        f"/users/{kavya}", data=_form(name="Kavya R.", email="kavya.r@example.com", role="demand_owner")
    )
    assert r.status_code == 400 and "at least one active lead admin" in r.text.lower()
    assert _m(db, "kavya.r@example.com").role == "admin"


def test_last_administrator_cannot_be_removed(admin: Client, db: Session) -> None:
    anil = user_id("anil")
    r = admin.post(
        f"/users/{anil}", data=_form(name="Anil V.", email="anil.v@example.com", role="leadership")
    )
    assert r.status_code == 400 and "at least one active Administrator" in r.text
    assert _m(db, "anil.v@example.com").role == "administrator"


def test_add_an_administrator(admin: Client, db: Session) -> None:
    r = admin.post("/users", data=_form(role="administrator", scope="own", bu_ids=[]))
    assert r.status_code == 303
    m = _m(db, "test.user@example.com")
    assert (m.role, m.visibility_scope) == ("administrator", "app_controls")
    assert _get(db, "test.user@example.com").business_units == []


def test_database_enforces_scope_rule(db: Session) -> None:
    m = _m(db, "priya.n@example.com")
    m.visibility_scope = "full"
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_users_api(admin: Client) -> None:
    users = admin.get("/api/users").json()
    assert len(users) == 12
    vikram = next(u for u in users if u["email"] == "vikram.p@example.com")
    assert vikram["scope"] == "assigned_interviews" and vikram["practices"] == ["CCA-FS"]
