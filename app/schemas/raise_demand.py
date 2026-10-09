from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator
from pydantic_core import PydanticUndefined

from app.schemas.user_access import _split


def _blank_to_none(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


class DemandForm(BaseModel):
    """What the Raise demand form posts. Account-specific lists (practice, grade, …) are checked in
    demand_service against account settings; this only checks shape."""

    bu_id: int | None = None
    name: str = Field(min_length=3, max_length=200)
    practice: str | None = None
    grade: str | None = None
    category: str | None = None
    type: Literal["New", "Replacement"] = "New"
    replaced_resource: str | None = Field(None, max_length=120)
    lwd: date | None = None  # replacement: the leaver's last working day
    position_type: Literal["Billable", "Non-billable"] = "Billable"
    # Does a panel select go on to a client interview, or is the panel's decision final?
    client_interview_required: bool = True
    primary_skills: list[str] = []
    secondary_skills: list[str] = []
    exp_min: int | None = Field(None, ge=0, le=50)
    exp_max: int | None = Field(None, ge=0, le=50)
    client_rate: Decimal | None = Field(None, ge=0, le=10000, decimal_places=2)
    start_date: date | None = None
    region: str | None = None
    location: str | None = Field(None, max_length=80)
    work_mode: str | None = None
    hiring_manager: str | None = Field(None, max_length=120)
    positions: int = Field(1, ge=1, le=20)
    jd_text: str | None = Field(None, max_length=12000)

    @field_validator("*", mode="before")
    @classmethod
    def _blanks(cls, v: Any, info: ValidationInfo) -> Any:
        """Empty form fields mean "not given": None, or the field's default where it has one."""
        v = _blank_to_none(v)
        if v is None and info.field_name:
            default = cls.model_fields[info.field_name].default
            return default if default is not PydanticUndefined else None
        return v

    @field_validator("primary_skills", "secondary_skills", mode="before")
    @classmethod
    def _skills(cls, v: Any) -> list[str]:
        return _split(v)

    @model_validator(mode="after")
    def _consistent(self) -> "DemandForm":
        if self.exp_min is not None and self.exp_max is not None and self.exp_min > self.exp_max:
            raise ValueError("Experience min is more than max")
        if self.type == "New":
            self.replaced_resource = self.lwd = None
        return self

    def missing_for_submit(self) -> list[str]:
        """Fields a demand must have before it goes to the admin."""
        need = {
            "Practice": self.practice,
            "Grade": self.grade,
            "Category": self.category,
            "Primary skills": self.primary_skills,
            "Requested start date": self.start_date,
            "Region": self.region,
            "Location": self.location,
            "Work mode": self.work_mode,
        }
        missing = [label for label, value in need.items() if not value]
        if self.type == "Replacement" and not self.replaced_resource:
            missing.append("Who is being replaced")
        if self.type == "Replacement" and not self.lwd:
            missing.append("Their last working day")
        return missing
