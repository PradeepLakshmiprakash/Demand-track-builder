"""Requests to the Administrator.

The Administrator runs the app's controls (User access, Account settings, Rate card) but doesn't
decide what changes. Anyone in the account asks here for what their role needs; the request is mailed
to the account's Administrators, who carry it out and mark it done or declined, which mails the
requester back. The Lead admin sees every request in the account; everyone else sees their own.

The Lead admin is a member of the GTD admin team: what they can do beyond the team (see rates, set
non-billable caps, manage interviewer profiles, raise an offer approval by hand) is special access
they asked the Administrator for, and it is on record here as "special" requests marked done.
"""

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.config import get_settings
from app.core.enums import EscalationType, MainStage, Responsible, Role, Severity
from app.core.security import Actor
from app.models import Account, AdminRequest, BusinessUnit, Demand, InterviewerProfile, User, member_of
from app.models.admin_request import KINDS
from app.services import demand_service, rate_card_service


class RequestError(ValueError):
    pass


def short(kind: str) -> str:
    return KINDS[kind].split(" (")[0]


# What each role can ask about, in the order offered. Requests are built from choices on the form (boxes
# ticked against what the person already has, lists, a number or a date), never from free text.
KINDS_FOR: dict[Role, tuple[str, ...]] = {
    Role.ADMIN: ("special", "access", "rate_card", "settings", "escalation", "profile", "data_fix"),
    Role.ADMIN_TEAM: ("access", "settings", "profile", "data_fix", "special"),
    Role.DEMAND_OWNER: ("access", "special", "data_fix", "settings"),
    Role.LEADERSHIP: ("access", "settings", "escalation", "rate_card", "special"),
    Role.INTERVIEWER: ("profile", "access", "special"),
}

# The special access each role can ask for, beyond what the role has as standard.
SPECIAL: dict[Role, tuple[str, ...]] = {
    Role.ADMIN: (
        "Lead admin access to see client bill rates, cost rates and margins on every demand in the account.",
        "Lead admin access to set the agreed non-billable cap for each business unit.",
        "Lead admin access to manage interviewer profiles (skills, practices, grade limit, availability).",
        "Lead admin access to raise an offer approval by hand when the BCM sheet has no candidate name.",
        "Lead admin access to see every request raised to the Administrator in this account.",
        "Lead admin access to the Rate card screen, read-only.",
    ),
    Role.ADMIN_TEAM: (
        "Read-only access to the Rate card screen.",
        "Access to manage interviewer profiles.",
        "Access to see client bill rates on demands.",
    ),
    Role.DEMAND_OWNER: (
        "Read access to the demands of another business unit I cover.",
        "Access to the Account overview for my business unit.",
        "Access to see the rate card costs for my practice.",
    ),
    Role.LEADERSHIP: (
        "A weekly export of the Account overview.",
        "Access to the Rate card screen, read-only.",
        "Access to the BCM sheet import history.",
    ),
    Role.INTERVIEWER: (
        "Access to the candidate's CV before the interview invite is sent.",
        "Access to the full demand page of requisitions I interview for.",
        "Access to earlier panel feedback on a candidate I am interviewing.",
    ),
}

# Account settings a request can be about: label, and whether the value is a number.
SETTINGS: dict[str, tuple[str, bool]] = {
    "add_practice": ("Add a practice to the list", False),
    "add_grade": ("Add a grade to the list", False),
    "add_work_mode": ("Add a work mode to the list", False),
    "add_region": ("Add a region to the list", False),
    "map_status": ("Map a new BCM sheet status to a stage", False),
    "margin": ("Change the margin cut-off for offer approval (%)", True),
    "grace": ("Change the grace period (working days)", True),
    "archive": ("Change the days before a finished demand is archived", True),
    "hours": ("Change the billable hours per day", True),
}

# What can be corrected on a demand: label, and how to read its current value.
DATA_FIELDS: dict[str, str] = {
    "client_rate": "Client bill rate",
    "start_date": "Requested start date",
    "gtd_req_id": "GTD requisition ID",
    "owner": "Demand owner",
    "practice": "Practice",
    "grade": "Grade",
    "bu": "Business unit",
}

ASSIGNABLE_ROLES = (Role.DEMAND_OWNER, Role.ADMIN_TEAM, Role.ADMIN, Role.LEADERSHIP, Role.INTERVIEWER)


def _diff(label: str, have: set[str], want: set[str]) -> str | None:
    add, remove = sorted(want - have), sorted(have - want)
    if not add and not remove:
        return None
    parts = ([f"add {', '.join(add)}"] if add else []) + ([f"remove {', '.join(remove)}"] if remove else [])
    return f"{label}: {'; '.join(parts)}."


def _current_value(db: Session, demand: Demand, field: str) -> str:
    if field == "client_rate":
        return f"${demand.client_rate:,.2f}" if demand.client_rate is not None else "not set"
    if field == "start_date":
        return f"{demand.start_date:%d %b %Y}" if demand.start_date else "not set"
    if field == "gtd_req_id":
        return demand.gtd_req_id or "not set"
    if field == "owner":
        return demand.owner.name
    if field == "bu":
        return demand.business_unit.name
    return str(getattr(demand, field) or "not set")


def compose(db: Session, actor: Actor, kind: str, form: Any) -> list[str]:
    """Turn the choices made on the form into the wording of one or more requests. Raises RequestError
    when nothing was actually chosen or changed."""
    if kind not in KINDS_FOR.get(actor.role, ()):
        raise RequestError("Choose what the request is about.")
    account = db.get_one(Account, actor.account_id)
    cfg = account.settings

    def one(key: str) -> str:
        return str(form.get(key) or "").strip()

    def many(key: str) -> set[str]:
        return {str(v).strip() for v in form.getlist(key) if str(v).strip()}

    if kind == "special":
        held = {r.details for r in special_access(db, actor.account_id, actor.id)}
        picked = [s for s in SPECIAL.get(actor.role, ()) if s in many("special") and s not in held]
        if not picked:
            raise RequestError("Tick the special access you need.")
        return picked

    if kind == "access":
        all_bus = set(db.scalars(select(BusinessUnit.name).where(BusinessUnit.account_id == account.id)))
        changes: list[str | None] = [
            _diff("Business units", set(actor.bu_names), many("bu") & all_bus),
            _diff("Practices", set(actor.practices), many("practice") & set(cfg.practices)),
        ]
        role = one("role")
        if role and role != actor.role.value:
            if role not in {r.value for r in ASSIGNABLE_ROLES}:
                raise RequestError("Choose a role from the list.")
            changes.append(f"Role: change from {actor.role.label} to {Role(role).label}.")
        said = [c for c in changes if c]
        if not said:
            raise RequestError("Nothing is changed: tick or untick what you need, or choose another role.")
        return [f"Access for {actor.name}: " + " ".join(said)]

    if kind == "rate_card":
        practice, grade = one("rc_practice"), one("rc_grade")
        if practice not in cfg.practices or grade not in cfg.grades:
            raise RequestError("Choose the practice and the grade.")
        try:
            cost = Decimal(one("rc_cost"))
            start = date.fromisoformat(one("rc_from"))
        except (InvalidOperation, ValueError) as e:
            raise RequestError("Enter the new cost per hour and the date it applies from.") from e
        if cost <= 0 or cost > 10000:
            raise RequestError("The cost per hour must be above 0.")
        now = rate_card_service.lookup(db, account.id, grade=grade, practice=practice, on=start)
        was = f" It is ${now.cost_rate:,.2f} today." if now is not None else " There is no rate for it today."
        return [f"Rate card: {grade} in {practice} to ${cost:,.2f} an hour from {start:%d %b %Y}.{was}"]

    if kind == "settings":
        what, value = one("st_what"), one("st_value")[:60]
        if what not in SETTINGS:
            raise RequestError("Choose which setting.")
        label, numeric = SETTINGS[what]
        if not value or (numeric and not value.replace(".", "", 1).isdigit()):
            raise RequestError("Enter the value" + (" as a number." if numeric else "."))
        extra = ""
        if what == "map_status":
            stage = one("st_stage")
            if stage not in {s.value for s in MainStage}:
                raise RequestError("Choose the stage the status maps to.")
            extra = f" → {MainStage(stage).label}"
        return [f"Account settings: {label}: {value}{extra}."]

    if kind == "escalation":
        trigger = one("es_trigger")
        if trigger not in {t.value for t in EscalationType}:
            raise RequestError("Choose the escalation.")
        rule = cfg.rule_for(trigger)
        moves: list[str] = []
        sev, who, on = one("es_severity"), one("es_responsible"), one("es_enabled")
        if sev and sev != rule.severity.value:
            moves.append(f"severity {rule.severity.label} → {Severity(sev).label}")
        if who and who != rule.responsible.value:
            moves.append(f"who acts {rule.responsible.label} → {Responsible(who).label}")
        if on in ("on", "off") and (on == "on") != rule.enabled:
            moves.append("turn it " + on)
        if not moves:
            raise RequestError("Nothing is changed: choose a different severity, who acts, or on/off.")
        return [f"Escalation rule '{EscalationType(trigger).label}': {'; '.join(moves)}."]

    if kind == "profile":
        target = profile_target(db, actor, one("pf_user"))
        if target is None:
            raise RequestError("Choose the interviewer.")
        user, prof = target
        edits: list[str | None] = [_diff("Skills", set(prof.skills or []), many("pf_skill"))]
        grade = one("pf_grade")
        if grade and grade != (prof.max_grade or ""):
            if grade not in cfg.grades:
                raise RequestError("Choose a grade from the list.")
            edits.append(f"Grade limit: {prof.max_grade or 'none'} → {grade}.")
        avail = one("pf_available")
        if avail in ("yes", "no") and (avail == "yes") != prof.active:
            edits.append("Mark as " + ("available" if avail == "yes" else "unavailable") + " for interviews.")
        told = [e for e in edits if e]
        if not told:
            raise RequestError(
                "Nothing is changed: tick the skills, or change the grade limit or availability."
            )
        return [f"Interviewer profile of {user.name}: " + " ".join(told)]

    # data_fix
    ref, field, value = one("df_demand"), one("df_field"), one("df_value")[:80]
    demand = demand_service.get_visible(db, actor, ref) if ref else None
    if demand is None:
        raise RequestError("Choose the demand.")
    if field not in DATA_FIELDS or not value:
        raise RequestError("Choose what is wrong and enter what it should be.")
    return [
        f"Data correction on {demand.app_ref} ({demand.name}): {DATA_FIELDS[field]} should be {value}. "
        f"It is {_current_value(db, demand, field)} now."
    ]


def profile_target(db: Session, actor: Actor, user_id: str) -> tuple[User, InterviewerProfile] | None:
    """Whose interviewer profile a request is about: the interviewer's own, or one the requester picked."""
    uid = actor.id if actor.role is Role.INTERVIEWER else (int(user_id) if user_id.isdigit() else 0)
    for user, prof in interviewers(db, actor.account_id):
        if user.id == uid:
            return user, prof
    return None


def interviewers(db: Session, account_id: int) -> list[tuple[User, InterviewerProfile]]:
    rows = db.execute(
        select(User, InterviewerProfile)
        .join(InterviewerProfile, InterviewerProfile.user_id == User.id)
        .where(member_of(account_id, Role.INTERVIEWER))
        .order_by(User.name)
    ).all()
    return [(u, p) for u, p in rows]


def skill_options(db: Session, account_id: int) -> list[str]:
    """Skills to tick on a profile request: every skill an interviewer profile or a demand already names."""
    seen: dict[str, str] = {}
    for _, prof in interviewers(db, account_id):
        for sk in prof.skills or []:
            seen.setdefault(sk.casefold(), sk)
    for skills in db.scalars(select(Demand.primary_skills).where(Demand.account_id == account_id)):
        for sk in skills or []:
            seen.setdefault(sk.casefold(), sk)
    return sorted(seen.values(), key=str.casefold)


def kinds_for(role: Role) -> dict[str, str]:
    """Request kinds this role is offered: key → label."""
    return {k: KINDS[k] for k in KINDS_FOR.get(role, ())}


def sees_all(actor: Actor) -> bool:
    return actor.role in (Role.ADMINISTRATOR, Role.ADMIN)


def list_requests(db: Session, account_id: int, only_by: int | None = None) -> list[AdminRequest]:
    stmt = select(AdminRequest).where(AdminRequest.account_id == account_id)
    if only_by is not None:
        stmt = stmt.where(AdminRequest.requested_by == only_by)
    return list(
        db.scalars(stmt.order_by((AdminRequest.status == "open").desc(), AdminRequest.created_at.desc()))
    )


def special_access(db: Session, account_id: int, user_id: int) -> list[AdminRequest]:
    """The special access this person asked for and was given."""
    return list(
        db.scalars(
            select(AdminRequest)
            .where(
                AdminRequest.account_id == account_id,
                AdminRequest.requested_by == user_id,
                AdminRequest.kind == "special",
                AdminRequest.status == "done",
            )
            .order_by(AdminRequest.handled_at)
        )
    )


def raise_request(db: Session, actor: Actor, kind: str, details: str) -> AdminRequest:
    details = details.strip()
    if kind not in KINDS_FOR.get(actor.role, ()):
        raise RequestError("Choose what the request is about.")
    if len(details) < 5:
        raise RequestError("Say what needs to change.")
    req = AdminRequest(account_id=actor.account_id, kind=kind, details=details, requested_by=actor.id)
    db.add(req)
    db.flush()
    account = db.get_one(Account, actor.account_id)
    admins = [u.email for u in db.scalars(select(User).where(member_of(account.id, Role.ADMINISTRATOR)))]
    link = f"{get_settings().app_base_url}/requests"
    mail.notify(
        mail.Mail(
            to=admins,
            cc=[actor.email],
            subject=f"[{account.name}] Request #{req.id}: {short(kind)}",
            text=(
                f"{actor.name} ({actor.role.label}) asks the Administrator for a change.\n\n"
                f"About: {KINDS[kind]}\n\n{details}\n\n"
                f"Steps: make the change on the matching screen, then mark request #{req.id} done:\n{link}"
            ),
        )
    )
    db.commit()
    return req


def handle(db: Session, actor: Actor, request_id: int, status: str, note: str | None) -> AdminRequest:
    req = db.get(AdminRequest, request_id)
    if req is None or req.account_id != actor.account_id:
        raise RequestError("Request not found.")
    if req.status != "open":
        raise RequestError("This request has already been handled.")
    if status not in ("done", "declined"):
        raise RequestError("Mark it done or declined.")
    note = (note or "").strip() or None
    if status == "declined" and not note:
        raise RequestError("Say why it's declined.")
    req.status, req.note = status, note
    req.handled_by, req.handled_at = actor.id, datetime.now(UTC)
    requester = db.get_one(User, req.requested_by)
    account = db.get_one(Account, req.account_id)
    mail.notify(
        mail.Mail(
            to=[requester.email],
            subject=f"[{account.name}] Request #{req.id} {status}",
            text=(
                f"Your request #{req.id} ({short(req.kind)}) was marked {status} by {actor.name}."
                + (f"\nNote: {note}" if note else "")
                + f"\n\nYou asked:\n{req.details}\n\n{get_settings().app_base_url}/requests"
            ),
        )
    )
    db.commit()
    return req
