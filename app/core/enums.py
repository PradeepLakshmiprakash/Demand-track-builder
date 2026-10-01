"""The app's fixed vocabulary.

These are the concepts the code reasons about. Anything that differs by client (BU names, practices,
how a DP sheet status maps to a stage) is account configuration instead, see `Account.config`.
"""

from enum import StrEnum


class Role(StrEnum):
    DEMAND_OWNER = "demand_owner"
    ADMIN = "admin"  # "GTD team admin" in the UI: heads the GTD admin team
    ADMIN_TEAM = "admin_team"  # "GTD admin team": the manual GTD and DP sheet work
    LEADERSHIP = "leadership"
    INTERVIEWER = "interviewer"
    # Runs the app's controls (settings, access, rate card) on request. Sees no demands.
    ADMINISTRATOR = "administrator"

    @property
    def label(self) -> str:
        return ROLE_LABELS[self]


ROLE_LABELS = {
    Role.DEMAND_OWNER: "Demand owner",
    Role.ADMIN: "GTD team admin",
    Role.ADMIN_TEAM: "GTD admin team",
    Role.LEADERSHIP: "Leadership",
    Role.INTERVIEWER: "Interviewer",
    Role.ADMINISTRATOR: "Administrator",
}


class Scope(StrEnum):
    """What slice of the account a user sees. Set on the User access page, never by login."""

    OWN = "own"
    OWN_BU_READ = "own_bu_read"
    FULL = "full"
    ASSIGNED_INTERVIEWS = "assigned_interviews"
    APP_CONTROLS = "app_controls"

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
    Scope.APP_CONTROLS: ("App controls", "Settings, access and rate card; no demands"),
}

# Which scopes each role may hold. A single allowed scope means the role is locked to it.
# Enforced three times: here (UI options), in user_service (writes) and by a CHECK constraint.
ALLOWED_SCOPES: dict[Role, tuple[Scope, ...]] = {
    Role.DEMAND_OWNER: (Scope.OWN, Scope.OWN_BU_READ),
    Role.ADMIN: (Scope.FULL,),
    Role.ADMIN_TEAM: (Scope.FULL,),
    Role.LEADERSHIP: (Scope.FULL,),
    Role.INTERVIEWER: (Scope.ASSIGNED_INTERVIEWS,),
    Role.ADMINISTRATOR: (Scope.APP_CONTROLS,),
}


class DemandStatus(StrEnum):
    # Before GTD (set by the app)
    DRAFT = "draft"
    SUBMITTED = "submitted"
    NOTIFIED = "notified"
    SENT_TO_GTD = "sent_to_gtd"
    RETURNED = "returned"  # sent back to the demand owner to correct, then resubmit
    # Linking (set by reconciliation)
    LINKED = "linked"
    MISSING = "missing"
    DROPPED = "dropped"
    INCORRECT = "incorrect"
    # Coverage (DP sheet status, mapped through account config)
    COVERAGE_REQUIRED = "coverage_required"
    # Interview progress, set by the app from panel records (the DP sheet lags; see pipeline_service)
    INTERVIEWING = "interviewing"
    PANEL_SELECTED = "panel_selected"
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
    DemandStatus.RETURNED: ("Returned for correction", "risk", "before_gtd"),
    DemandStatus.LINKED: ("Linked", "teal", "linking"),
    DemandStatus.MISSING: ("Missing from sheet", "esc", "linking"),
    DemandStatus.DROPPED: ("Dropped from sheet", "esc", "linking"),
    DemandStatus.INCORRECT: ("Incorrect demand", "esc", "linking"),
    DemandStatus.COVERAGE_REQUIRED: ("Coverage required", "teal", "coverage"),
    DemandStatus.INTERVIEWING: ("Interviewing", "teal", "coverage"),
    DemandStatus.PANEL_SELECTED: ("Selected by panel", "blue", "coverage"),
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

# Coverage stages in the order a demand moves through them. The app may move a demand forward on
# interview evidence; the DP sheet may move it anywhere (pipeline_service decides who wins).
PROGRESS_ORDER: tuple[DemandStatus, ...] = (
    DemandStatus.LINKED,
    DemandStatus.COVERAGE_REQUIRED,
    DemandStatus.INTERVIEWING,
    DemandStatus.PANEL_SELECTED,
    DemandStatus.PROFILES_WITH_CLIENT,
    DemandStatus.OFFER_IN_PROCESS,
    DemandStatus.OFFER_IN_MARKET,
    DemandStatus.STAFFED,
)
APP_PROGRESS = frozenset({DemandStatus.INTERVIEWING, DemandStatus.PANEL_SELECTED})

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
    RESUBMIT = "resubmit"  # back into the next admin mail; the new GTD ID chains to the old one
    EXTEND = "extend"  # stays open at L1 with a new due date
    CLOSE = "close"  # the demand is closed
    NO_ACTION = "no_action"  # the condition has already cleared (e.g. the ID was linked late)
    RETURN = "return"  # back to the demand owner to correct; they resubmit

    @property
    def label(self) -> str:
        return {
            "resubmit": "Resubmit in next admin mail",
            "extend": "Extend due date",
            "close": "Close demand",
            "no_action": "No further action (condition cleared)",
            "return": "Send back to demand owner for correction",
        }[self.value]


class EscalationEventKind(StrEnum):
    OPENED = "opened"
    NOTIFIED = "notified"
    PROMOTED = "promoted"
    EXTENDED = "extended"
    RESOLVED = "resolved"


class StageOrigin(StrEnum):
    APP = "app"
    IMPORT = "import"


class RowOutcome(StrEnum):
    """What reconciliation made of one DP sheet row."""

    MATCHED = "matched"  # tier 1: its requisition ID is linked to a demand
    PREFIX = "prefix"  # tier 2: [DM-…] in the name; linked automatically
    CONFIRMED = "confirmed"  # tier 3 or manual: a person matched it, or created a demand from it
    SUGGESTED = "suggested"  # unmatched, with fuzzy suggestions waiting for a person
    UNMATCHED = "unmatched"  # unmatched, no suggestion
    CONFLICT = "conflict"  # its [DM-…] demand is already linked to a different requisition ID
    DUPLICATE = "duplicate"  # the same requisition ID appears earlier in the sheet
    INVALID = "invalid"  # no requisition ID
    SUPERSEDED = "superseded"  # an ID the demand has since replaced (resubmitted); ignored

    @property
    def needs_person(self) -> bool:
        return self in (RowOutcome.SUGGESTED, RowOutcome.UNMATCHED, RowOutcome.CONFLICT)


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


class InterviewStatus(StrEnum):
    REQUESTED = "requested"  # L2 asked for by the panelist; waiting for the demand owner
    DECLINED = "declined"  # the demand owner said no to the extra round
    OPEN = "open"  # needed, but staffing hasn't scheduled it or no interviewer is known yet
    SCHEDULED = "scheduled"  # interviewer assigned (time may still be unknown); feedback link issued
    COMPLETED = "completed"  # recommendation recorded

    @property
    def label(self) -> str:
        return {
            "requested": "Waiting for demand owner",
            "declined": "Extra round declined",
            "open": "To be scheduled",
            "scheduled": "Scheduled",
            "completed": "Feedback in",
        }[self.value]


class InterviewSource(StrEnum):
    APP = "app"
    KARAT = "karat"


class CandidateSource(StrEnum):
    SHEET = "sheet"  # the DP sheet's candidate name on the requisition's row
    MANUAL = "manual"  # added by the GTD admin team or a panelist
    KARAT = "karat"


def check_in(column: str, values: type[StrEnum] | tuple[StrEnum, ...]) -> str:
    """SQL for a CHECK constraint limiting a text column to an enum's values."""
    items = ", ".join(f"'{v.value}'" for v in values)
    return f"{column} IN ({items})"
