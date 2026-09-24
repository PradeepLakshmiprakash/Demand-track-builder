from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.enums import Role, Scope


def _split(v: Any) -> list[str]:
    """Accept a comma-separated string or a list; trim, drop blanks, keep order, de-duplicate."""
    items = v if isinstance(v, list) else str(v or "").split(",")
    return list(dict.fromkeys(s.strip() for s in items if str(s).strip()))


class UserForm(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    role: Role
    level: str | None = Field(None, max_length=10)
    scope: Scope | None = None
    bu_ids: list[int] = []
    practices: list[str] = []
    skills: list[str] = []  # interviewers only
    max_grade: str | None = None  # interviewers only

    @field_validator("name", "level", "max_grade", mode="before")
    @classmethod
    def _strip(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v

    @field_validator("practices", "skills", mode="before")
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _split(v)

    @field_validator("scope", mode="before")
    @classmethod
    def _blank_scope(cls, v: Any) -> Any:
        return v or None


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    role: Role
    level: str | None
    scope: Scope
    active: bool
    business_units: list[str]
    practices: list[str]
