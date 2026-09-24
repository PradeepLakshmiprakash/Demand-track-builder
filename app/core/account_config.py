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


# DP sheet columns the app reads: field → (default header in the Discover sheet, required?).
DP_FIELDS: dict[str, tuple[str, bool]] = {
    "req_id": ("Code Requisition", True),
    "demand_request_name": ("Demand Request Name", True),
    "status": ("Status", True),
    "status_group": ("Status Group", True),
    "originator": ("ORIGINATOR_NAME", False),
    "practice": ("Practice", False),
    "grade": ("Local Grade", False),
    "region": ("Region", False),
    "start_date": ("Position Start Date", False),
    "gettalent_req_id": ("GetTalent - Job Req ID", False),
    "source": ("Source", False),
    "candidate_name": ("Candidate Name", False),
    "candidate_details": ("External Candidate Details", False),
    "doj": ("DOJ", False),
}
DP_FIELD_LABELS = {
    "req_id": "GTD requisition ID",
    "demand_request_name": "Demand request name",
    "status": "Status",
    "status_group": "Status group",
    "originator": "Originator (demand owner)",
    "practice": "Practice",
    "grade": "Grade",
    "region": "Region",
    "start_date": "Position start date",
    "gettalent_req_id": "GetTalent requisition ID",
    "source": "Supply source",
    "candidate_name": "Candidate name",
    "candidate_details": "Candidate details",
    "doj": "Date of joining",
}


def _default_dp_columns() -> dict[str, str]:
    return {k: header for k, (header, _) in DP_FIELDS.items()}


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
    # Revenue lost = hourly bill rate × these hours × working days late (flow-artifact §10).
    billable_hours_per_day: float = Field(8.0, gt=0, le=24)
    dp_columns: dict[str, str] = Field(default_factory=_default_dp_columns)
    # Cell values the DP sheet uses for "empty".
    dp_blank_values: list[str] = ["0", "-"]
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
