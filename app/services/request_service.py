"""Requests to the Administrator.

The Administrator runs the app's controls (User access, Account settings, Rate card) but doesn't
decide what changes. Anyone in the account asks here for what their role needs; the request is mailed
to the account's Administrators, who carry it out and mark it done or declined, which mails the
requester back. The Lead admin sees every request in the account; everyone else sees their own.

The Lead admin is a member of the GTD admin team: what they can do beyond the team (see rates, set
non-billable caps, manage interviewer profiles, raise an offer approval by hand) is special access
they asked the Administrator for, and it is on record here as "special" requests marked done.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import mail
from app.core.config import get_settings
from app.core.enums import Role
from app.core.security import Actor
from app.models import Account, AdminRequest, User, member_of
from app.models.admin_request import KINDS


class RequestError(ValueError):
    pass


def short(kind: str) -> str:
    return KINDS[kind].split(" (")[0]


# What each role can ask about, in the order offered, with an example of a request it might make.
KINDS_FOR: dict[Role, dict[str, str]] = {
    Role.ADMIN: {
        "special": "Lead admin access to see client bill rates and margins on every demand",
        "access": "Add Asha P. (asha.p@example.com) as a demand owner in CARDS",
        "rate_card": "C2 in DCX-FS goes to $73.00 an hour from 1 Nov 2026",
        "settings": "Add the practice Cloud-Data to the practice list",
        "escalation": "Make 'Missing from sheet' high severity, to be answered in 1 working day",
        "profile": "Add Kafka to Vikram P.'s interview skills",
        "data_fix": "DM-000131 was linked to the wrong requisition ID; unlink it so it can be redone",
        "other": "",
    },
    Role.ADMIN_TEAM: {
        "access": "I also cover the DATA business unit from Monday: please add it to my access",
        "settings": "The BCM sheet has a new status 'On Hold - Client': please map it to a stage",
        "profile": "Anita G. is on leave until 20 Oct: mark her unavailable for interviews",
        "data_fix": "A requisition ID was typed wrongly on DM-000149: it should be 7QWZ2L",
        "special": "Access to the Rate card screen, read-only, to answer staffing's questions",
        "other": "",
    },
    Role.DEMAND_OWNER: {
        "access": "I have taken over the PAYMENTS demands from Priya N.: please move them to me",
        "special": "Read access to the BANKING demands, which I cover while Meera S. is away",
        "settings": "Add 'Remote - Canada' to the work mode list",
        "escalation": "Add the reason 'Client hiring freeze' for past start date escalations",
        "data_fix": "The client bill rate on DM-000121 should be $98.00, not $95.00",
        "other": "",
    },
    Role.LEADERSHIP: {
        "access": "Give Ritu S. leadership access to this account",
        "settings": "Lower the margin cut-off for offer approval from 30% to 28%",
        "escalation": "Inform me of overdue escalations for high severity only",
        "rate_card": "Review the Cloud-Java costs: they look higher than last quarter's",
        "special": "A weekly export of the account overview for the client governance call",
        "other": "",
    },
    Role.INTERVIEWER: {
        "profile": "Add Kafka and Spark to my interview skills, and raise my grade limit to C2",
        "access": "I have moved to the DATA practice: please update my practice",
        "data_fix": "I submitted feedback against the wrong candidate; please reopen the interview",
        "special": "Access to the candidate's CV before the interview invite is sent",
        "other": "",
    },
}


def kinds_for(role: Role) -> dict[str, str]:
    """Request kinds this role is offered: key → label."""
    return {k: KINDS[k] for k in KINDS_FOR.get(role, {})}


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
    if kind not in KINDS_FOR.get(actor.role, {}):
        raise RequestError("Choose what the request is about.")
    if len(details) < 5:
        raise RequestError("Say what needs to change.")
    req = AdminRequest(account_id=actor.account_id, kind=kind, details=details, requested_by=actor.id)
    db.add(req)
    db.flush()
    account = db.get_one(Account, actor.account_id)
    admins = [u.email for u in db.scalars(select(User).where(member_of(account.id, Role.ADMINISTRATOR)))]
    link = f"{get_settings().app_base_url}/requests"
    mail.send(
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
    mail.send(
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
