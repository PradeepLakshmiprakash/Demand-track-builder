from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base
from app.core.enums import Role, Scope, check_in
from app.models.account import Account, BusinessUnit


class User(Base):
    """A person. Role and visibility are data set on the User access page; login only identifies them."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(check_in("role", Role), name="role_valid"),
        CheckConstraint(check_in("visibility_scope", Scope), name="scope_valid"),
        # The access rule itself, enforced by the database (mirrors enums.ALLOWED_SCOPES).
        CheckConstraint(
            "(role IN ('admin', 'admin_team', 'leadership') AND visibility_scope = 'full')"
            " OR (role = 'interviewer' AND visibility_scope = 'assigned_interviews')"
            " OR (role = 'demand_owner' AND visibility_scope IN ('own', 'own_bu_read'))",
            name="scope_matches_role",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20))
    level: Mapped[str | None] = mapped_column(String(10))
    visibility_scope: Mapped[str] = mapped_column(String(24))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    accounts: Mapped[list[Account]] = relationship(secondary="user_accounts", order_by=Account.id)
    business_units: Mapped[list[BusinessUnit]] = relationship(
        secondary="user_business_units", order_by=BusinessUnit.id
    )
    practice_links: Mapped[list["UserPractice"]] = relationship(cascade="all, delete-orphan")
    interviewer_profile: Mapped["InterviewerProfile | None"] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def role_enum(self) -> Role:
        return Role(self.role)

    @property
    def scope_enum(self) -> Scope:
        return Scope(self.visibility_scope)

    @property
    def practices(self) -> list[str]:
        return sorted(p.practice for p in self.practice_links)


# Emails are unique regardless of case.
Index("uq_users_email_lower", func.lower(User.email), unique=True)


class UserAccount(Base):
    __tablename__ = "user_accounts"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)


class UserBusinessUnit(Base):
    __tablename__ = "user_business_units"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    bu_id: Mapped[int] = mapped_column(ForeignKey("business_units.id"), primary_key=True)


class UserPractice(Base):
    __tablename__ = "user_practices"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    practice: Mapped[str] = mapped_column(String(40), primary_key=True)


class InterviewerProfile(Base):
    __tablename__ = "interviewer_profiles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    practices: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    max_grade: Mapped[str | None] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="interviewer_profile")
