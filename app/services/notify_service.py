"""The daily admin mail (flow-artifact §4).

At each account's mail time, the GTD team admin and the GTD admin team get the demands submitted since
the last mail (status → notified) plus a reminder of earlier ones still without a requisition ID.
Each mail is recorded as a notification batch. It goes out once per account per day unless forced.
"""

from dataclasses import dataclass
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core import mail
from app.core.config import get_settings
from app.core.enums import DemandStatus, Role
from app.core.workdays import add_working_days
from app.models import Account, Demand, NotificationBatch, User, UserAccount
from app.services.demand_service import record_stage

ADMIN_ROLES = (Role.ADMIN.value, Role.ADMIN_TEAM.value)


@dataclass
class MailResult:
    batch: NotificationBatch | None
    message: str


def local_now(account: Account, now: datetime | None = None) -> datetime:
    tz = ZoneInfo(account.settings.timezone)
    return (now or datetime.now(tz)).astimezone(tz)


def sent_today(db: Session, account: Account, now: datetime | None = None) -> bool:
    local = local_now(account, now)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        db.scalar(
            select(NotificationBatch.id).where(
                NotificationBatch.account_id == account.id, NotificationBatch.sent_at >= start
            )
        )
        is not None
    )


def is_due(db: Session, account: Account, now: datetime | None = None) -> bool:
    return local_now(account, now).time() >= account.mail_time and not sent_today(db, account, now)


def recipients(db: Session, account_id: int) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .join(UserAccount, (UserAccount.user_id == User.id) & (UserAccount.account_id == account_id))
            .where(User.active, UserAccount.active, UserAccount.role.in_(ADMIN_ROLES))
            .order_by(UserAccount.role, User.name)
        )
    )


def _demands(db: Session, account_id: int, status: DemandStatus) -> list[Demand]:
    return list(
        db.scalars(
            select(Demand)
            .where(Demand.account_id == account_id, Demand.status == status.value)
            .options(
                selectinload(Demand.business_unit),
                selectinload(Demand.owner),
                selectinload(Demand.submissions),
            )
            .order_by(Demand.submitted_at, Demand.app_ref)
        )
    )


def send_daily_admin_mail(
    db: Session, account_id: int, *, now: datetime | None = None, force: bool = False
) -> MailResult:
    account = db.get_one(Account, account_id)
    if not force and sent_today(db, account, now):
        return MailResult(None, "Today's admin mail has already gone out.")

    new = _demands(db, account.id, DemandStatus.SUBMITTED)
    waiting = _demands(db, account.id, DemandStatus.NOTIFIED)
    if not new and not waiting:
        return MailResult(None, "Nothing to send: no demands are waiting for GTD entry.")
    to = recipients(db, account.id)
    if not to:
        return MailResult(None, "No active GTD team admin or GTD admin team member to send to.")

    local = local_now(account, now)
    batch = NotificationBatch(account_id=account.id, sent_at=local, demand_ids=[d.id for d in new])
    db.add(batch)
    for d in new:
        record_stage(db, d, DemandStatus.NOTIFIED, actor_id=None)
    db.flush()

    try:
        mail.send(compose(account, new, waiting, [u.email for u in to], local))
    except Exception:
        db.rollback()  # demands stay "submitted" and go in the next attempt
        raise
    db.commit()
    return MailResult(batch, f"Admin mail sent to {len(to)}: {len(new)} new, {len(waiting)} still waiting.")


def compose(
    account: Account, new: list[Demand], waiting: list[Demand], to: list[str], when: datetime
) -> mail.Mail:
    day = when.strftime("%d %b")
    link = f"{get_settings().app_base_url}/gtd-queue"
    subject = f"[Demand Tracker] {account.name} · {len(new)} to enter on GTD · {day}"
    if not new:
        subject = (
            f"[Demand Tracker] {account.name} · {len(waiting)} still without a GTD requisition ID · {day}"
        )

    def facts(d: Demand) -> str:
        return f"{d.business_unit.name} · {d.practice or '—'} · {d.grade or '—'}"

    def start(d: Demand) -> str:
        return d.start_date.strftime("%d %b %Y") if d.start_date else "no start date"

    def line(d: Demand) -> str:
        again = f" · resubmit, was {d.gtd_req_id}" if d.submissions else ""
        return f"  {d.gtd_name}\n    {facts(d)} · start {start(d)} · owner {d.owner.name}{again}"

    def rows(ds: list[Demand]) -> str:
        td = "<td style='padding:6px 10px'>{}</td>"
        cells = "".join(
            "<tr>"
            + td.format(f"<span style='font-family:monospace'>{escape(d.app_ref)}</span>")
            + "".join(td.format(escape(v)) for v in (d.gtd_name, facts(d), start(d), d.owner.name))
            + "</tr>"
            for d in ds
        )
        return f"<table style='border-collapse:collapse;font:14px sans-serif'>{cells}</table>"

    h3 = "<h3 style='font:600 15px sans-serif'>{}</h3>"
    text = [f"Demands to enter on GTD for {account.name}, {day}.", ""]
    intro = f"Demands to enter on GTD for <b>{escape(account.name)}</b>, {day}."
    html = [f"<p style='font:15px sans-serif'>{intro}</p>"]
    if new:
        text += [f"New since the last mail ({len(new)}):", *map(line, new), ""]
        html += [h3.format(f"New since the last mail ({len(new)})"), rows(new)]
    if waiting:
        text += [f"Still without a requisition ID ({len(waiting)}):", *map(line, waiting), ""]
        html += [h3.format(f"Still without a requisition ID ({len(waiting)})"), rows(waiting)]
    cutoff = add_working_days(when.date(), 1).strftime("%d %b")
    text += [
        "Enter each on GTD with the name exactly as shown, then paste the requisition ID on the GTD queue:",
        link,
        f"Demands without an ID by the next mail ({cutoff}) are escalated as not submitted.",
    ]
    html += [
        f"<p style='font:14px sans-serif'>Enter each on GTD with the name exactly as shown, then paste the "
        f"requisition ID on the <a href='{link}'>GTD queue</a>. Demands without an ID by the next mail "
        f"({cutoff}) are escalated as not submitted.</p>"
    ]
    return mail.Mail(to=to, subject=subject, text="\n".join(text), html="".join(html))
