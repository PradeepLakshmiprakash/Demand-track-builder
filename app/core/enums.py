"""The app's fixed vocabulary.

These are the concepts the code reasons about. Anything that differs by client (BU names, practices,
how a DP sheet status maps to a stage) is account configuration instead, see `Account.config`.
"""

from enum import StrEnum


class Role(StrEnum):
    DEMAND_OWNER = "demand_owner"
    ADMIN = "admin"  # "Admin demand owner" in the UI
    ADMIN_TEAM = "admin_team"  # the admin demand owner's team: the manual admin work
    LEADERSHIP = "leadership"
    INTERVIEWER = "interviewer"

    @property
    def label(self) -> str:
        return ROLE_LABELS[self]


ROLE_LABELS = {
    Role.DEMAND_OWNER: "Demand owner",
    Role.ADMIN: "Admin demand owner",
    Role.ADMIN_TEAM: "Admin team",
    Role.LEADERSHIP: "Leadership",
    Role.INTERVIEWER: "Interviewer",
}


class Scope(StrEnum):
    """What slice of the account a user sees. Set on the User access page, never by login."""

    OWN = "own"
    OWN_BU_READ = "own_bu_read"
    FULL = "full"
    ASSIGNED_INTERVIEWS = "assigned_interviews"

    @property
    def label(self) -> str:
        return SCOPE_LABELS[self][0]

    @property
    def description(self) -> str:
        return SCOPE_LABELS[self][1]


SCOPE_LABELS = {
    Scope.OWN: ("Own demands", "Only demands they raised"),
    Scope.OWN_BU_READ: ("Own + BU read-only", "Can view other demands in their BU(s)"),
    Scope.FULL: ("Full account", "All BUs, all demands"),
    Scope.ASSIGNED_INTERVIEWS: ("Assigned interviews", "Plus alerts for new requisitions in their skills"),
}

# Which scopes each role may hold. A single allowed scope means the role is locked to it.
# Enforced three times: here (UI options), in user_service (writes) and by a CHECK constraint.
ALLOWED_SCOPES: dict[Role, tuple[Scope, ...]] = {
    Role.DEMAND_OWNER: (Scope.OWN, Scope.OWN_BU_READ),
    Role.ADMIN: (Scope.FULL,),
    Role.ADMIN_TEAM: (Scope.FULL,),
    Role.LEADERSHIP: (Scope.FULL,),
    Role.INTERVIEWER: (Scope.ASSIGNED_INTERVIEWS,),
}


class DemandStatus(StrEnum):
    # Before GTD (set by the app)
    DRAFT = "draft"
    SUBMITTED = "submitted"
    NOTIFIED = "notified"
    SENT_TO_GTD = "sent_to_gtd"
    # Linking (set by reconciliation)
    LINKED = "linked"
    MISSING = "missing"
    DROPPED = "dropped"
    INCORRECT = "incorrect"
    # Coverage (DP sheet status, mapped through account config)
    COVERAGE_REQUIRED = "coverage_required"
    PROFILES_WITH_CLIENT = "profiles_with_client"
    OFFER_IN_PROCESS = "offer_in_process"
    OFFER_IN_MARKET = "offer_in_market"
    STAFFED = "staffed"
    # End
    CANCELLED = "cancelled"
    CLOSED = "closed"

    @property
    def label(self) -> str:
        return STATUS_META[self][0]

    @property
    def chip(self) -> str:
        return STATUS_META[self][1]

    @property
    def phase(self) -> str:
        return STATUS_META[self][2]


# label, chip colour, phase
STATUS_META: dict[DemandStatus, tuple[str, str, str]] = {
    DemandStatus.DRAFT: ("Draft", "gray", "before_gtd"),
    DemandStatus.SUBMITTED: ("Submitted", "gray", "before_gtd"),
    DemandStatus.NOTIFIED: ("In admin mail", "gray", "before_gtd"),
    DemandStatus.SENT_TO_GTD: ("Sent to GTD", "blue", "before_gtd"),
    DemandStatus.LINKED: ("Linked", "teal", "linking"),
    DemandStatus.MISSING: ("Missing from sheet", "esc", "linking"),
    DemandStatus.DROPPED: ("Dropped from sheet", "esc", "linking"),
    DemandStatus.INCORRECT: ("Incorrect demand", "esc", "linking"),
    DemandStatus.COVERAGE_REQUIRED: ("Coverage required", "teal", "coverage"),
    DemandStatus.PROFILES_WITH_CLIENT: ("Profiles with client", "teal", "coverage"),
    DemandStatus.OFFER_IN_PROCESS: ("Offer in process", "blue", "coverage"),
    DemandStatus.OFFER_IN_MARKET: ("Offer in market", "blue", "coverage"),
    DemandStatus.STAFFED: ("Staffed", "done", "coverage"),
    DemandStatus.CANCELLED: ("Cancelled", "gray", "end"),
    DemandStatus.CLOSED: ("Closed", "gray", "end"),
}

BEFORE_GTD = frozenset(s for s in DemandStatus if s.phase == "before_gtd")
LINK_PROBLEMS = frozenset({DemandStatus.MISSING, DemandStatus.DROPPED, DemandStatus.INCORRECT})
# No further work expected: not "open", never "past start".
FINISHED = frozenset({DemandStatus.STAFFED, DemandStatus.CANCELLED, DemandStatus.CLOSED})

# Stages a DP sheet status may map to (account settings → status mapping).
SHEET_STAGES: tuple[DemandStatus, ...] = (
    DemandStatus.COVERAGE_REQUIRED,
    DemandStatus.PROFILES_WITH_CLIENT,
    DemandStatus.OFFER_IN_PROCESS,
    DemandStatus.OFFER_IN_MARKET,
    DemandStatus.STAFFED,
    DemandStatus.CANCELLED,
    DemandStatus.INCORRECT,
)


class EscalationType(StrEnum):
    NOT_SUBMITTED = "not_submitted"
    MISSING = "missing"
    DROPPED = "dropped"
    INCORRECT = "incorrect"
    AGING = "aging"
    REJECTION_LIMIT = "rejection_limit"
    PANEL_SLA = "panel_sla"
    PAST_START = "past_start"

    @property
    def label(self) -> str:
        return {
            "not_submitted": "Not submitted",
            "missing": "Missing from sheet",
            "dropped": "Dropped from sheet",
            "incorrect": "Incorrect demand",
            "aging": "Aging",
            "rejection_limit": "Rejection limit",
            "panel_sla": "Panel SLA",
            "past_start": "Past start date",
        }[self.value]

    @property
    def short(self) -> str:
        """For status chips: 'Missing · escalated L1'."""
        return {"missing": "Missing", "dropped": "Dropped", "past_start": "Past start"}.get(
            self.value, self.label
        )


class EscalationStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class ResolutionAction(StrEnum):
    RESUBMIT = "resubmit"
    EXTEND = "extend"
    CLOSE = "close"


class StageOrigin(StrEnum):
    APP = "app"
    IMPORT = "import"


class ApprovalRoute(StrEnum):
    ADMIN = "admin"
    LEADERSHIP = "leadership"


class Decision(StrEnum):
    APPROVED = "approved"
    DECLINED = "declined"


class InterviewOutcome(StrEnum):
    SELECT = "select"
    REJECT = "reject"
    HOLD = "hold"


def check_in(column: str, values: type[StrEnum] | tuple[StrEnum, ...]) -> str:
    """SQL for a CHECK constraint limiting a text column to an enum's values."""
    items = ", ".join(f"'{v.value}'" for v in values)
    return f"{column} IN ({items})"
