"""Requests to the Administrator.

The Administrator runs the app's controls (User access, Account settings, Rate card) but doesn't
decide what changes. The GTD team admin raises each change here; it is mailed to the account's
Administrators, who carry it out and mark it done or declined, which mails the requester back.
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


def list_requests(db: Session, account_id: int) -> list[AdminRequest]:
    return list(
        db.scalars(
            select(AdminRequest)
            .where(AdminRequest.account_id == account_id)
            .order_by((AdminRequest.status == "open").desc(), AdminRequest.created_at.desc())
        )
    )


def raise_request(db: Session, actor: Actor, kind: str, details: str) -> AdminRequest:
    details = details.strip()
    if kind not in KINDS:
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
