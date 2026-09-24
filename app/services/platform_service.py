"""Accounts: the platform admin creates a client account, names its first admin demand owner, and can
deactivate it. Everything else about the client is set by that admin in the account's own settings.

A new account starts either blank (defaults only) or with a copy of another account's settings: lists,
channels, DP sheet columns and status mapping, and thresholds. Business units, people, rate card and
demands are never copied: they belong to the client.
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.account_config import AccountConfig
from app.core.enums import FINISHED, Role, Scope
from app.core.security import Actor
from app.models import Account, Demand, User, UserAccount
from app.services.user_service import interviewer_conflict

THRESHOLDS = (
    "grace_days", "l1_sla_days", "l2_sla_days", "panel_timer_hours", "aging_days", "rejection_limit",
    "margin_threshold", "mail_time",
)  # fmt: skip


class PlatformError(ValueError):
    pass


@dataclass
class AccountRow:
    account: Account
    members: int
    open_demands: int
    admins: list[str]


def list_accounts(db: Session) -> list[AccountRow]:
    out = []
    for a in db.scalars(select(Account).order_by(Account.active.desc(), Account.name)):
        members = db.scalar(
            select(func.count())
            .select_from(UserAccount)
            .where(UserAccount.account_id == a.id, UserAccount.active)
        )
        open_demands = db.scalar(
            select(func.count(Demand.id)).where(
                Demand.account_id == a.id, Demand.status.not_in([s.value for s in FINISHED])
            )
        )
        admins = list(
            db.scalars(
                select(User.name)
                .join(UserAccount, UserAccount.user_id == User.id)
                .where(
                    UserAccount.account_id == a.id, UserAccount.role == Role.ADMIN.value, UserAccount.active
                )
                .order_by(User.name)
            )
        )
        out.append(AccountRow(a, members or 0, open_demands or 0, admins))
    return out


def create_account(
    db: Session,
    actor: Actor,
    *,
    name: str,
    timezone: str,
    copy_from: int | None,
    admin_name: str,
    admin_email: str,
) -> Account:
    name, admin_name, admin_email = name.strip(), admin_name.strip(), admin_email.strip()
    if len(name) < 2:
        raise PlatformError("Give the account a name.")
    if db.scalar(select(Account.id).where(func.lower(Account.name) == name.lower())):
        raise PlatformError(f"There is already an account called {name}.")
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as e:
        raise PlatformError(f"Unknown time zone: {timezone}") from e
    if len(admin_name) < 2 or "@" not in admin_email:
        raise PlatformError("Name the account's first admin demand owner, with their email.")

    account = Account(name=name, active=True)
    if copy_from:
        source = db.get(Account, copy_from)
        if source is None:
            raise PlatformError("The account to copy settings from doesn't exist.")
        for col in THRESHOLDS:
            setattr(account, col, getattr(source, col))
        cfg = source.settings.model_copy(deep=True)
    else:
        cfg = AccountConfig.model_validate({})
    cfg.timezone = timezone
    account.settings = cfg
    db.add(account)
    db.flush()

    admin = db.scalar(select(User).where(func.lower(User.email) == admin_email.lower()))
    if admin is not None and (why := interviewer_conflict(admin, account.id, Role.ADMIN)):
        raise PlatformError(why)
    if admin is None:
        admin = User(name=admin_name, email=admin_email, active=True, created_by=actor.id)
        db.add(admin)
    admin.memberships.append(
        UserAccount(
            account_id=account.id, role=Role.ADMIN.value, visibility_scope=Scope.FULL.value, active=True
        )
    )
    db.commit()
    return account


def set_account_active(db: Session, actor: Actor, account_id: int, active: bool) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise PlatformError("No such account.")
    if not active and account_id == actor.account_id:
        raise PlatformError("You're working in this account. Switch to another account first.")
    account.active = active
    db.commit()
    return account
