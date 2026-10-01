"""Phase 8: a second account runs with no client-specific code.

- Acme Insurance (seeded) keeps its data apart from Discover NA, reads its own DP sheet format, prices
  offers from its own rate card and routes them with its own margin cut-off.
- People who work in both accounts have one login, a role per account and an account switcher.
- Exit: the platform admin creates a third account (Globex Retail) and its admin sets it up entirely
  through the screens, then a demand goes from raise to an approved offer.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.models import Account, Demand, OfferApproval, User, UserAccount
from app.services import escalation_service
from seed import sample_sheet_acme
from seed.sample_sheet_acme import build_sheet
from tests.conftest import Client, account_id, bu_id, user_id

ACME, DISCOVER = "Acme Insurance", "Discover NA"


def demand(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def upload(client: Client, data: bytes, day: date | None = None) -> None:
    r = client.post(
        "/imports",
        data={"sheet_date": (day or date.today()).isoformat()},
        files={"file": ("sheet.xlsx", data, "application/octet-stream")},
    )
    assert r.status_code == 303, r.text
    assert "err=" not in r.headers["location"], r.headers["location"]


# --- Isolation ----------------------------------------------------------------------------------------


def test_accounts_do_not_see_each_other(client: Client) -> None:
    discover = client.as_user("kavya").get("/api/demands").json()
    assert "DM-000101" not in {d["app_ref"] for d in discover} and len(discover) == 18
    assert client.get("/demands/DM-000101").status_code == 404

    acme = client.as_user("grace").get("/api/demands").json()
    assert {d["app_ref"] for d in acme} == {f"DM-00010{i}" for i in range(1, 7)}
    assert client.get("/demands/DM-000142").status_code == 404
    page = client.as_user("rosa").get("/settings").text  # Acme's Administrator
    assert "Req #" in page and "Offer Pending" in page and "Code Requisition" not in page
    rates = client.get("/rate-card").text
    assert "Partner network" in rates and "Sogeti" not in rates


def test_someone_outside_an_account_cannot_switch_into_it(client: Client) -> None:
    assert client.as_user("priya").get(f"/switch-account/{account_id(ACME)}").status_code == 404


# --- People in both accounts ----------------------------------------------------------------------------


def test_shared_person_has_a_role_per_account_and_switches(client: Client) -> None:
    c = client.as_user("sanjay")
    assert DISCOVER in c.get("/overview").text
    r = c.get(f"/switch-account/{account_id(ACME)}")
    assert r.status_code == 303
    overview = c.get("/overview").text
    assert ACME in overview and "Switch account" in overview
    assert {d["app_ref"] for d in c.get("/api/demands").json()} >= {"DM-000101", "DM-000103"}

    # Interviewers stay inside their one account.
    assert "Candidate A" in client.as_user("vikram").get("/interviews").text
    assert "Candidate P" not in client.get("/interviews").text
    nadia = client.as_user("nadia").get("/interviews").text
    assert "Candidate P" in nadia and "Candidate A" not in nadia


def _person(email: str, role: str, bu: str = "CLAIMS") -> dict[str, object]:
    scope = {"demand_owner": "own", "interviewer": "assigned_interviews"}.get(role, "full")
    return {
        "name": "ignored",
        "email": email,
        "role": role,
        "level": "L4",
        "scope": scope,
        "bu_ids": [str(bu_id(bu))],
        "practices": ["APP-ENG"],
        "skills": "Java",
    }


def test_an_interviewer_cannot_join_a_second_account(client: Client, db: Session) -> None:
    grace = client.as_user("rosa")
    for role in ("interviewer", "demand_owner", "leadership"):
        r = grace.post("/users", data=_person("vikram.p@example.com", role))
        assert r.status_code == 400 and "is an interviewer in Discover NA" in r.text, role
    vikram = db.scalars(select(User).where(User.email == "vikram.p@example.com")).one()
    assert [m.account.name for m in vikram.memberships] == [DISCOVER]


def test_someone_in_another_account_cannot_become_an_interviewer(client: Client) -> None:
    r = client.as_user("rosa").post("/users", data=_person("priya.n@example.com", "interviewer"))
    assert r.status_code == 400 and "already works in Discover NA" in r.text
    # and a shared person can't be switched to interviewer in either account
    form = {
        "name": "Sanjay M.",
        "email": "sanjay.m@example.com",
        "role": "interviewer",
        "level": "L6",
        "scope": "assigned_interviews",
        "bu_ids": [str(bu_id("CLAIMS"))],
        "practices": ["APP-ENG"],
    }
    r = client.post(f"/users/{user_id('sanjay')}", data=form)
    assert r.status_code == 400 and "already works in Discover NA" in r.text


def test_an_interviewer_cannot_be_a_new_accounts_admin(client: Client) -> None:
    form = {
        "name": "Nova Co",
        "timezone": "America/Chicago",
        "copy_from": "",
        "admin_name": "Vikram P.",
        "admin_email": "vikram.p@example.com",
    }
    r = client.as_user("anil").post("/platform/accounts", data=form)
    assert "err=" in r.headers["location"] and "interviewer" in r.headers["location"]
    assert "Nova Co" not in client.get("/platform/accounts").text


def test_adding_an_existing_person_gives_them_a_second_account(client: Client, db: Session) -> None:
    form = {
        "name": "ignored",
        "email": "PRIYA.N@example.com",
        "role": "demand_owner",
        "level": "L4",
        "scope": "own",
        "bu_ids": [str(bu_id("CLAIMS"))],
    }
    r = client.as_user("rosa").post("/users", data=form)
    assert r.status_code == 303, r.text
    priya = db.scalars(select(User).where(User.email == "priya.n@example.com")).one()
    assert priya.name == "Priya N."  # the existing person, not renamed
    roles = {m.account.name: (m.role, m.level) for m in priya.memberships}
    assert roles == {DISCOVER: ("demand_owner", "C2"), ACME: ("demand_owner", "L4")}
    assert "also in Discover NA" in client.get(f"/users?id={priya.id}").text


def test_deactivating_in_one_account_leaves_the_other(client: Client) -> None:
    sanjay = user_id("sanjay")
    assert client.as_user("rosa").post(f"/users/{sanjay}/active", data={"active": "0"}).status_code == 303
    c = client.as_user("sanjay")
    assert c.get("/overview").status_code == 200  # Discover
    assert c.get(f"/switch-account/{account_id(ACME)}").status_code == 404


# --- Acme's own rules -------------------------------------------------------------------------------------


def test_acme_reads_its_own_sheet_format_and_rate_card(client: Client, db: Session) -> None:
    client.as_user("tomas")  # Acme GTD admin team
    upload(client, sample_sheet_acme.build())
    assert demand(db, "DM-000102").status == "coverage_required"  # "Sourcing" → coverage required
    assert demand(db, "DM-000101").status == "interviewing"  # Candidate P's panel is scheduled
    assert demand(db, "DM-000105").status == "offer_in_market"
    offer = db.scalars(
        select(OfferApproval).where(OfferApproval.demand_id == demand(db, "DM-000103").id)
    ).one()
    # L5 through the partner network: cost 75, bill 100 → 25%, which is Acme's cut-off → admin decides.
    assert (offer.channel, offer.cost_rate, offer.margin_pct) == (
        "partner",
        Decimal("75.00"),
        Decimal("25.00"),
    )
    assert offer.route == "admin"
    page = client.get("/reconciliation").text
    assert "AC9001" in page  # raised outside the app: waits for a person
    assert "Date of joining" in client.as_user("grace").get("/demands/DM-000105").text


def test_sweeps_and_mails_run_per_account(db: Session) -> None:
    for a in db.scalars(select(Account)):
        escalation_service.sweep(db, a.id)
    # A missing Acme ID is escalated with Acme's owner labels, never Discover's.
    acme = db.scalars(select(Account).where(Account.name == ACME)).one()
    assert acme.settings.escalation_owners["L1"] == "Practice lead"


# --- Exit: a third client onboarded through the screens only ------------------------------------------------


def _post(c: Client, path: str, data: dict[str, Any]) -> None:
    r = c.post(path, data=data)
    assert r.status_code == 303, (path, r.text[:400])
    assert "err=" not in r.headers.get("location", ""), (path, r.headers["location"])


def test_phase8_exit_new_account_through_settings_only(client: Client, db: Session) -> None:
    mail.sent.clear()
    # 1. Only a platform admin may create accounts.
    assert client.as_user("grace").get("/platform/accounts").status_code == 403
    admin_form = {
        "name": "Globex Retail",
        "timezone": "Europe/London",
        "copy_from": "",
        "admin_name": "Pat Q.",
        "admin_email": "pat.q@example.com",
    }
    _post(client.as_user("anil"), "/platform/accounts", admin_form)
    assert "Globex Retail" in client.get("/platform/accounts").text

    # 2. The Administrator (whoever created the account) sets it up in Account settings.
    assert client.as_user("pat").get("/settings").status_code == 403  # the GTD team admin asks, never edits
    pat = client.as_user("anil", "Globex Retail")
    assert pat.get("/settings").status_code == 200
    _post(pat, "/settings/business-units", {"name": "Stores"})
    _post(
        pat,
        "/settings/lists",
        {
            "practices": "RETAIL-APPS\nANALYTICS",
            "grades": "G1\nG2\nG3",
            "regions": "UK",
            "work_modes": "Onsite\nHybrid",
            "categories": "Open",
            "resolution_reasons": "Unknown\nWithdrawn by client",
            "interview_ratings": "Craft\nJudgement",
            "escalation_owner_l1": "Store lead",
            "escalation_owner_l2": "Client director",
        },
    )
    _post(
        pat,
        "/settings/supply-channels",
        {"label": ["Agency"], "sheet_marker": ["Agency"], "needs_sourcing_req": [""]},
    )
    _post(
        pat,
        "/settings/status-mapping",
        {
            "status_group": ["Open", "Open", "Offer"],
            "status": ["Searching", "Shortlisted", "Offer Out"],
            "stage": ["coverage_required", "profiles_with_client", "offer_in_process"],
        },
    )
    columns = {
        "req_id": "Requisition",
        "demand_request_name": "Role",
        "status": "State",
        "status_group": "Phase",
        "grade": "Band",
        "practice": "Practice",
        "start_date": "Start",
        "source": "Supplier",
        "candidate_name": "Hire",
        "region": "Country",
    }
    _post(
        pat, "/settings/dp-columns", {**{f"col_{k}": v for k, v in columns.items()}, "blank_values": "none"}
    )
    _post(
        pat,
        "/settings/thresholds",
        {
            "grace_days": "2",
            "l1_sla_days": "2",
            "l2_sla_days": "2",
            "panel_timer_hours": "24",
            "aging_days": "10",
            "rejection_limit": "2",
            "margin_threshold": "20",
            "mail_time": "07:30",
            "timezone": "Europe/London",
            "billable_hours_per_day": "7",
        },
    )
    _post(
        pat,
        "/rate-card",
        {
            "grade": "G2",
            "practice": "",
            "region": "UK",
            "channel": "agency",
            "cost_rate": "50",
            "effective_from": "2026-01-01",
        },
    )

    # 3. People: a new demand owner, and someone from Discover's GTD admin team who helps here too.
    _post(
        pat,
        "/users",
        {
            "name": "Uma S.",
            "email": "uma.s@example.com",
            "role": "demand_owner",
            "level": "G2",
            "scope": "own",
            "bu_ids": [str(bu_id("STORES"))],
        },
    )
    _post(
        pat,
        "/users",
        {"name": "x", "email": "farah.q@example.com", "role": "admin_team", "level": "G2", "scope": "full"},
    )

    # 4. A demand from raise to an approved offer.
    start = (date.today() + timedelta(days=40)).isoformat()
    uma = client.as_user("uma")
    r = uma.post(
        "/demands/new",
        data={
            "practice": "RETAIL-APPS",
            "grade": "G2",
            "name": "Store Systems Developer",
            "category": "Open",
            "type": "New",
            "position_type": "Billable",
            "client_interview_required": "false",
            "primary_skills": "Java",
            "client_rate": "80",
            "start_date": start,
            "region": "UK",
            "location": "London",
            "work_mode": "Hybrid",
            "positions": "1",
            "action": "submit",
        },
    )
    assert r.status_code == 303, r.text[:400]
    ref = r.headers["location"].split("/")[2].split("?")[0]
    d = demand(db, ref)
    assert d.account_id == account_id("Globex Retail") and d.status == "submitted"

    farah = client.as_user("farah", "Globex Retail")  # works here as GTD admin team too
    _post(farah, "/gtd-queue/send-mail", {})
    assert any("Globex Retail" in m.subject for m in mail.sent)
    _post(farah, f"/gtd-queue/{d.id}/link", {"gtd_req_id": "GX0001"})

    def row(state: str, phase: str, **kw: Any) -> dict[str, Any]:
        return {
            "req_id": "GX0001",
            "demand_request_name": f"[{ref}] Store Systems Developer",
            "status": state,
            "status_group": phase,
            "grade": "G2",
            "practice": "RETAIL-APPS",
            "region": "UK",
            **kw,
        }

    upload(farah, build_sheet(columns, [row("Searching", "Open")], blank="none"))
    assert demand(db, ref).status == "coverage_required"
    upload(
        farah,
        build_sheet(
            columns, [row("Offer Out", "Offer", source="Agency", candidate_name="Candidate Z")], blank="none"
        ),
        date.today() + timedelta(days=1),
    )
    assert demand(db, ref).status == "offer_in_process"
    offer = db.scalars(select(OfferApproval).where(OfferApproval.demand_id == d.id)).one()
    assert (offer.cost_rate, offer.margin_pct, offer.route) == (Decimal("50.00"), Decimal("37.50"), "admin")
    _post(client.as_user("pat"), f"/approvals/{offer.id}/decide", {"decision": "approved", "comment": ""})
    assert any(
        "Offer for Candidate Z approved" in m.subject for m in mail.sent if m.to == ["uma.s@example.com"]
    )
    assert "Globex Retail" in client.get("/overview").text
    escalation_service.sweep(db, account_id("Globex Retail"))

    # 5. Still isolated, and Farah is still GTD admin team in Discover.
    assert client.as_user("kavya").get(f"/demands/{ref}").status_code == 404
    assert client.as_user("pat").get("/demands/DM-000142").status_code == 404
    farah_roles = {
        m.account.name: m.role
        for m in db.scalars(select(UserAccount).where(UserAccount.user_id == user_id("farah")))
    }
    assert farah_roles == {DISCOVER: "admin_team", "Globex Retail": "admin_team"}


def test_deactivated_account_blocks_its_people(client: Client) -> None:
    acme = account_id(ACME)
    kavya = client.as_user("anil")
    _post(kavya, f"/platform/accounts/{acme}/active", {"active": "0"})
    assert client.as_user("grace").get("/demands").status_code == 403  # Acme only
    c = client.as_user("sanjay")
    assert c.get("/overview").status_code == 200  # still has Discover
    assert c.get(f"/switch-account/{acme}").status_code == 404
