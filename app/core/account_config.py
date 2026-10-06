"""Typed shape of `accounts.config` (JSONB).

Every client-specific list or mapping lives here so a second account is onboarded through the
Account settings screen, not code. Numeric thresholds are real columns on `accounts` instead,
because the escalation sweep filters on them in SQL.
"""

from pydantic import BaseModel, Field, field_validator

from app.core.enums import SHEET_STAGES, DemandStatus, Responsible, Severity


class SupplyChannel(BaseModel):
    key: str
    label: str
    sheet_marker: str = Field(description="Text in the BCM sheet that identifies this channel")
    needs_sourcing_req: bool = Field(False, description="Needs a GetTalent requisition and a sourcer")


class StatusMapping(BaseModel):
    status_group: str = ""
    status: str
    stage: DemandStatus

    @field_validator("stage")
    @classmethod
    def _sheet_stage_only(cls, v: DemandStatus) -> DemandStatus:
        if v not in SHEET_STAGES:
            raise ValueError(f"{v.label} can't be set from the BCM sheet")
        return v


class EscalationRule(BaseModel):
    """One trigger's rule: is it on, who must act, how urgent, and the steps the email spells out."""

    enabled: bool = True
    responsible: Responsible
    severity: Severity
    steps: str
    reasons: list[str] = []  # what the responder picks from; empty = the defaults for this trigger


DEFAULT_RULES: dict[str, tuple[Responsible, Severity, str]] = {
    "not_submitted": (
        Responsible.GTD_TEAM,
        Severity.MEDIUM,
        "Create the demand on GTD and link its requisition ID on the GTD queue.",
    ),
    "missing": (
        Responsible.GTD_TEAM,
        Severity.MEDIUM,
        "Check on GTD whether the requisition was approved. Link the right ID, or send the demand back "
        "to its owner to correct.",
    ),
    "dropped": (
        Responsible.DEMAND_OWNER,
        Severity.HIGH,
        "Confirm whether the position is still needed. If yes, resubmit it; if not, close the demand with "
        "the reason.",
    ),
    "incorrect": (
        Responsible.DEMAND_OWNER,
        Severity.MEDIUM,
        "Open the demand, correct what was flagged, and resubmit it.",
    ),
    "aging": (
        Responsible.DEMAND_OWNER,
        Severity.LOW,
        "Review the requirements with staffing. Update the demand, ask for more time with a reason, or "
        "close it.",
    ),
    "rejection_limit": (
        Responsible.DEMAND_OWNER,
        Severity.MEDIUM,
        "Review the job description and the bar with the panel. Update the demand, or close it.",
    ),
    "panel_sla": (
        Responsible.INTERVIEWER,
        Severity.LOW,
        "Submit your feedback for the interview, from your invite link or My interviews.",
    ),
    "unlinked_row": (
        Responsible.GTD_TEAM,
        Severity.MEDIUM,
        "Match the sheet row to its demand, or create the demand from the row, on the Reconciliation "
        "page. If the requisition isn't ours, close this with the reason.",
    ),
    "past_start": (
        Responsible.DEMAND_OWNER,
        Severity.HIGH,
        "Give a revised start date, or confirm the date of joining with staffing. Close the demand if it "
        "is no longer needed.",
    ),
}


# The reasons a responder picks from, per trigger: each list answers "why is this late / wrong?" for
# that kind of escalation. "Other" is always offered last and needs a comment.
OTHER_REASON = "Other"
DEFAULT_REASONS: dict[str, list[str]] = {
    "not_submitted": [
        "Created on GTD now",
        "Waiting for details from the demand owner",
        "GTD was unavailable",
        "Duplicate demand",
    ],
    "missing": [
        "Approval still pending on GTD",
        "Wrong requisition ID was linked",
        "Failed GTD basic checks",
        "Approved late: will be in the next BCM sheet",
        "Duplicate demand",
    ],
    "dropped": [
        "Removed by mistake: position still needed",
        "Position no longer needed",
        "Withdrawn by client",
        "Replaced by another requisition",
    ],
    "incorrect": [
        "Details corrected",
        "Failed GTD basic checks",
        "Duplicate demand",
        "Withdrawn by client",
    ],
    "aging": [
        "No coverage available",
        "Requirements being revised",
        "Waiting for the client's feedback",
        "Client budget pending",
        "Position no longer needed",
    ],
    "rejection_limit": [
        "Job description revised",
        "Bar reviewed with the panel",
        "Rate or grade being revised",
        "Scarce skill: sourcing widened",
        "Position no longer needed",
    ],
    "panel_sla": [
        "Feedback submitted",
        "Interviewer unavailable: interview reassigned",
        "Interview did not take place",
    ],
    "unlinked_row": [
        "Belongs to another account",
        "Old requisition, already closed",
        "Duplicate of an existing demand",
    ],
    "past_start": [
        "Candidate selected: joining date agreed",
        "Offer in progress",
        "Client moved the start date",
        "Still sourcing: no suitable profile yet",
        "Client budget pending",
        "Withdrawn by client",
        "Position no longer needed",
    ],
}


# A starting guess at which tech stack each practice takes (the mapping Acquisition Central uses, read
# off the practice names), for an account that hasn't set its own. Keys are lower case.
DEFAULT_PRACTICE_STACKS: dict[str, tuple[str, ...]] = {
    "cca-fs": ("Front end and mobile", "Cloud and DevOps", "Data and integration", "Testing and QA",
               "BA / delivery / architecture"),
    "dcx-fs": ("Salesforce", "Data and integration", "Front end and mobile"),
    "dmn-fs": ("Guidewire", "Data and integration", "BA / delivery / architecture"),
    "tes-fs": ("Testing and QA",),
    "adm-fs": ("Data and integration", "Cloud and DevOps", "ServiceNow / Workday / SAP"),
    "cloud-java": ("Cloud and DevOps",),
    "cloud-mf": ("Cloud and DevOps",),
    "cloud-apm": ("Cloud and DevOps", "ServiceNow / Workday / SAP"),
}  # fmt: skip


def _default_rules() -> dict[str, EscalationRule]:
    return {k: EscalationRule(responsible=r, severity=s, steps=t) for k, (r, s, t) in DEFAULT_RULES.items()}


# BCM sheet columns the app reads: field → (default header in the Discover sheet, required?).
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
    # The tech stack each practice takes, set by the Administrator (Account settings). Two practices
    # that share a tech stack can take the same person; the margin calculator compares them. A practice
    # the account hasn't listed here falls back to DEFAULT_PRACTICE_STACKS, if it is named there.
    practice_stacks: dict[str, list[str]] = {}
    status_mapping: list[StatusMapping] = []
    escalation_owners: dict[str, str] = {"L1": "LOB delivery head", "L2": "Account leadership"}
    # Escalations (flow-artifact §9): the responsible person acts; everyone else is informed.
    escalation_rules: dict[str, EscalationRule] = Field(default_factory=_default_rules)
    # Working days the responsible person has to respond, by severity. After that it is overdue (L2).
    response_days: dict[str, int] = {"high": 1, "medium": 2, "low": 3}
    # Who is informed when an escalation goes overdue. They don't act; the responsible person does.
    l2_inform_leadership: bool = True
    l2_inform_delivery_head: bool = True
    # What a panelist rates, each 1 to 5 (flow-artifact §7).
    interview_ratings: list[str] = ["Technical depth", "Problem solving", "Communication"]
    # Revenue lost = hourly bill rate × these hours × working days late (flow-artifact §10).
    billable_hours_per_day: float = Field(8.0, gt=0, le=24)
    # A joined or abandoned demand leaves the lists and the overview this many days after it finished.
    archive_after_days: int = Field(30, ge=1, le=365)
    dp_columns: dict[str, str] = Field(default_factory=_default_dp_columns)
    # Cell values the BCM sheet uses for "empty".
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
        """Map a BCM sheet row's status to an app stage. Exact status match wins; group is a tiebreak."""
        s = (status or "").strip().casefold()
        g = (status_group or "").strip().casefold()
        hits = [m for m in self.status_mapping if m.status.strip().casefold() == s]
        for m in hits:
            if m.status_group.strip().casefold() == g:
                return m.stage
        return hits[0].stage if hits else None

    def rule_for(self, trigger: str) -> EscalationRule:
        """The account's rule for a trigger; the default for one the account hasn't set."""
        return self.escalation_rules.get(trigger) or _default_rules()[trigger]

    def reasons_for(self, trigger: str) -> list[str]:
        """The reasons offered when responding to this kind of escalation; "Other" always comes last."""
        own = self.rule_for(trigger).reasons or DEFAULT_REASONS[trigger]
        return [*(r for r in own if r != OTHER_REASON), OTHER_REASON]

    def stacks_of(self, practice: str) -> list[str]:
        """The tech stack this practice takes."""
        want = practice.casefold()
        for name, stacks in self.practice_stacks.items():
            if name.casefold() == want:
                return list(stacks)
        return list(DEFAULT_PRACTICE_STACKS.get(want, ()))

    def shared_stacks(self, a: str, b: str) -> list[str]:
        """The tech stacks both practices take, in the first one's wording; empty when they share none."""
        theirs = {s.casefold() for s in self.stacks_of(b)}
        return [s for s in self.stacks_of(a) if s.casefold() in theirs]

    def grade_rank(self, grade: str) -> int:
        try:
            return self.grades.index(grade)
        except ValueError:
            return -1
