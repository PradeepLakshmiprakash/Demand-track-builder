"""User access: who exists, their role, BUs, practices and what they see.

Rules (flow-artifact §1.1):
- Admin demand owners and leadership always see the full account, all BUs. Fixed.
- Demand owners see their own demands; the admin may widen that to own + BU read-only.
- Interviewers see assigned interviews; their skills, practices and max grade live on their profile.
- Deactivating removes access immediately; demands and history stay.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.enums import ALLOWED_SCOPES, Role, Scope
from app.core.security import Actor
from app.models import Account, BusinessUnit, InterviewerProfile, User, UserPractice
from app.schemas.user_access import UserForm


class UserAccessError(ValueError):
    pass


def effective_scope(role: Role, requested: Scope | None) -> Scope:
    allowed = ALLOWED_SCOPES[role]
    return requested if requested in allowed else allowed[0]


def list_users(db: Session, account_id: int) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .where(User.accounts.any(id=account_id))
            .options(
                selectinload(User.business_units),
                selectinload(User.practice_links),
                selectinload(User.interviewer_profile),
            )
            .order_by(User.active.desc(), User.name)
        )
    )


def get_user(db: Session, account_id: int, user_id: int) -> User:
    user = db.scalar(select(User).where(User.id == user_id, User.accounts.any(id=account_id)))
    if user is None:
        raise UserAccessError("User not found in this account.")
    return user


def _apply(db: Session, actor: Actor, user: User, form: UserForm) -> None:
    account_bus = list(
        db.scalars(
            select(BusinessUnit).where(BusinessUnit.account_id == actor.account_id, BusinessUnit.active)
        )
    )
    clash = db.scalar(
        select(User.id).where(func.lower(User.email) == form.email.lower(), User.id != (user.id or 0))
    )
    if clash:
        raise UserAccessError(f"{form.email} is already a user.")

    user.name = form.name
    user.email = form.email
    user.role = form.role.value
    user.level = form.level
    user.visibility_scope = effective_scope(form.role, form.scope).value

    if ALLOWED_SCOPES[form.role] == (Scope.FULL,):
        user.business_units = account_bus  # locked: full account, every BU
    else:
        chosen = [b for b in account_bus if b.id in set(form.bu_ids)]
        if form.role is Role.DEMAND_OWNER and not chosen:
            raise UserAccessError("A demand owner needs at least one business unit.")
        # Keep BUs from other accounts untouched.
        others = [b for b in user.business_units if b.account_id != actor.account_id]
        user.business_units = others + chosen

    user.practice_links = [UserPractice(practice=p) for p in dict.fromkeys(form.practices)]

    if form.role is Role.INTERVIEWER:
        profile = user.interviewer_profile or InterviewerProfile()
        profile.practices = list(form.practices)
        profile.skills = list(form.skills)
        profile.max_grade = form.max_grade
        profile.active = True
        user.interviewer_profile = profile
    elif user.interviewer_profile is not None:
        user.interviewer_profile.active = False


def _guard_last_admin(db: Session, actor: Actor, user: User, *, losing_admin: bool) -> None:
    if not losing_admin:
        return
    others = db.scalar(
        select(func.count(User.id)).where(
            User.role == Role.ADMIN.value,
            User.active,
            User.id != user.id,
            User.accounts.any(id=actor.account_id),
        )
    )
    if not others:
        raise UserAccessError("The account needs at least one active admin demand owner.")


def create_user(db: Session, actor: Actor, form: UserForm) -> User:
    user = User(created_by=actor.id, active=True)
    user.accounts = [db.get_one(Account, actor.account_id)]
    _apply(db, actor, user, form)
    db.add(user)
    db.commit()
    return user


def update_user(db: Session, actor: Actor, user_id: int, form: UserForm) -> User:
    user = get_user(db, actor.account_id, user_id)
    was_admin = user.role == Role.ADMIN.value and user.active
    _guard_last_admin(db, actor, user, losing_admin=was_admin and form.role is not Role.ADMIN)
    _apply(db, actor, user, form)
    db.commit()
    return user


def set_active(db: Session, actor: Actor, user_id: int, active: bool) -> User:
    user = get_user(db, actor.account_id, user_id)
    if not active:
        if user.id == actor.id:
            raise UserAccessError("You can't deactivate yourself.")
        _guard_last_admin(db, actor, user, losing_admin=user.role == Role.ADMIN.value)
    user.active = active
    db.commit()
    return user
