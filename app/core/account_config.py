"""Typed shape of `accounts.config` (JSONB).

Every client-specific list or mapping lives here so a second account is onboarded through the
Account settings screen, not code. Numeric thresholds are real columns on `accounts` instead,
because the escalation sweep filters on them in SQL.
"""

from pydantic import BaseModel, Field, field_validator

from app.core.enums import SHEET_STAGES, DemandStatus


class SupplyChannel(BaseModel):
    key: str
    label: str
    sheet_marker: str = Field(description="Text in the DP sheet that identifies this channel")
    needs_sourcing_req: bool = Field(False, description="Needs a GetTalent requisition and a sourcer")


class StatusMapping(BaseModel):
    status_group: str = ""
    status: str
    stage: DemandStatus

    @field_validator("stage")
    @classmethod
    def _sheet_stage_only(cls, v: DemandStatus) -> DemandStatus:
        if v not in SHEET_STAGES:
            raise ValueError(f"{v.label} can't be set from the DP sheet")
        return v


class AccountConfig(BaseModel):
    timezone: str = "America/Chicago"
    practices: list[str] = []
    grades: list[str] = Field(
        default=[], description="Lowest to highest; interviewer max grade uses this order"
    )
    regions: list[str] = ["US", "CA"]
    work_modes: list[str] = ["Onsite", "Hybrid", "Remote"]
    categories: list[str] = ["Open", "Proactive"]
    supply_channels: list[SupplyChannel] = []
    status_mapping: list[StatusMapping] = []
    escalation_owners: dict[str, str] = {"L1": "LOB delivery head", "L2": "Account leadership"}
    resolution_reasons: list[str] = [
        "Failed GTD basic checks",
        "No coverage available",
        "Duplicate demand",
        "Withdrawn by client",
        "Client budget pending",
        "Unknown",
    ]

    def stage_for(self, status_group: str | None, status: str | None) -> DemandStatus | None:
        """Map a DP sheet row's status to an app stage. Exact status match wins; group is a tiebreak."""
        s = (status or "").strip().casefold()
        g = (status_group or "").strip().casefold()
        hits = [m for m in self.status_mapping if m.status.strip().casefold() == s]
        for m in hits:
            if m.status_group.strip().casefold() == g:
                return m.stage
        return hits[0].stage if hits else None

    def grade_rank(self, grade: str) -> int:
        try:
            return self.grades.index(grade)
        except ValueError:
            return -1
