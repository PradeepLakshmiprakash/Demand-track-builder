"""The workflow diagram: every main stage as a container, every sub-stage as a box inside it.

The same picture is used twice: on the account overview with the number of demands at each box, and
for one demand with the steps it has passed ticked and its current step filled in. The drawing is
done in the browser (static/workflow.js); this module says what the boxes are and in what order.
"""

from typing import Any

from app.core.enums import DemandStatus, MainStage
from app.models import Demand

S = DemandStatus

# The normal path through each main stage, in order.
PATH: dict[MainStage, tuple[DemandStatus, ...]] = {
    MainStage.COVERAGE: (S.DRAFT, S.SUBMITTED, S.SENT_TO_GTD, S.LINKED, S.COVERAGE_REQUIRED),
    MainStage.SELECTION: (S.INTERVIEWING, S.PANEL_SELECTED, S.PROFILES_WITH_CLIENT),
    MainStage.ALLOC_PENDING: (S.OFFER_IN_PROCESS, S.OFFER_IN_MARKET),
    MainStage.ALLOC_DONE: (S.STAFFED,),
}
# Problem states, each drawn beside the step of the first stage it belongs to (index into its path).
PROBLEMS: tuple[tuple[DemandStatus, int], ...] = (
    (S.RETURNED, 1),
    (S.MISSING, 2),
    (S.DROPPED, 3),
    (S.INCORRECT, 4),
)
ENDS: tuple[DemandStatus, ...] = (S.CANCELLED, S.CLOSED)

# What happens next, and who does it, shown under one demand's diagram.
NEXT: dict[DemandStatus, str] = {
    S.DRAFT: "The demand owner submits it to the GTD admin team.",
    S.SUBMITTED: "The GTD admin team creates it on GTD and links the requisition ID.",
    S.NOTIFIED: "The GTD admin team creates it on GTD and links the requisition ID.",
    S.RETURNED: "The demand owner corrects the demand and resubmits it.",
    S.SENT_TO_GTD: "GTD approves it; it then appears in the next BCM sheet.",
    S.MISSING: "The GTD admin team checks GTD: link the right ID, or send it back to the owner.",
    S.DROPPED: "The demand owner confirms whether the position is still needed.",
    S.INCORRECT: "The demand owner corrects what was flagged and resubmits it.",
    S.LINKED: "Staffing starts sourcing profiles; the BCM sheet shows the status.",
    S.COVERAGE_REQUIRED: "Staffing sources profiles; the panel interviews them.",
    S.INTERVIEWING: "The panel gives its feedback on each candidate.",
    S.PANEL_SELECTED: "The client interviews the candidate; the demand owner marks it started.",
    S.PROFILES_WITH_CLIENT: "The demand owner records the client's decision, or the BCM sheet moves it on.",
    S.OFFER_IN_PROCESS: "The offer is approved: by the demand owner, or by leadership below the cut-off.",
    S.OFFER_IN_MARKET: "The candidate joins; the BCM sheet confirms the date of joining.",
    S.STAFFED: "Nothing: the candidate has joined.",
    S.CANCELLED: "Nothing: the requisition was cancelled in the BCM sheet.",
    S.CLOSED: "Nothing: the demand was closed.",
}


def layout() -> dict[str, Any]:
    """The boxes, by label, for static/workflow.js."""
    first = MainStage.COVERAGE
    return {
        "stages": [
            {
                "label": stage.label,
                "subs": [s.label for s in path],
                "problems": [[s.label, row] for s, row in PROBLEMS] if stage is first else [],
            }
            for stage, path in PATH.items()
        ],
        "abandoned": {"label": MainStage.ABANDONED.label, "subs": [s.label for s in ENDS]},
    }


def position(demand: Demand) -> dict[str, str]:
    s = demand.status_enum
    return {"stage": s.main.label, "sub": s.label}


def next_step(demand: Demand) -> str:
    return NEXT[demand.status_enum]
