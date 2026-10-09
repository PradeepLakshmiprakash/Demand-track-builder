"""The workflow diagram: every main stage as a container, every sub-stage as a box inside it.

The same picture is used twice: on the account overview with the number of demands at each box, and
for one demand with the steps it has passed ticked and its current step filled in. The drawing is
done in the browser (static/workflow.js); this module says what the boxes are and in what order.
"""

from typing import Any
from zoneinfo import ZoneInfo

from app.core.enums import DemandStatus, EscalationType, MainStage
from app.models import Account, Demand, StageEvent

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


# Joined, but the owner hasn't confirmed the first billable day: the last step of client onboarding.
# It is shown as a step, without being a demand status of its own.
BILLING_PENDING = "Joined, billing to be confirmed"


def layout() -> dict[str, Any]:
    """The boxes, by label, for static/workflow.js."""
    first = MainStage.COVERAGE
    return {
        "stages": [
            {
                "label": stage.label,
                "subs": [s.label for s in path]
                + ([BILLING_PENDING] if stage is MainStage.ALLOC_PENDING else []),
                "problems": [[s.label, row] for s, row in PROBLEMS] if stage is first else [],
            }
            for stage, path in PATH.items()
        ],
        "abandoned": {"label": MainStage.ABANDONED.label, "subs": [s.label for s in ENDS]},
    }


def position(demand: Demand) -> dict[str, str]:
    """Where a demand is. A joined position whose billing isn't confirmed is at the last step of client
    onboarding."""
    s = demand.status_enum
    if demand.awaits_billing:
        return {"stage": MainStage.ALLOC_PENDING.label, "sub": BILLING_PENDING}
    return {"stage": s.main.label, "sub": s.label}


def next_step(demand: Demand) -> str:
    if demand.awaits_billing:
        return "The demand owner confirms the first billable day."
    return NEXT[demand.status_enum]


def number(status: DemandStatus) -> int:
    """The step's number on the normal path, counted across the stages (Draft is 1)."""
    order = [s for path in PATH.values() for s in path]
    n = order.index(status) + 1
    return n + 1 if n > order.index(S.OFFER_IN_MARKET) + 1 else n  # the billing step sits before Joined


def reached(demand: Demand, history: list[StageEvent], tz: str | None) -> dict[str, Any]:
    """For one demand's diagram: the day it reached each step, and the steps it never takes."""
    zone = ZoneInfo(tz) if tz else None
    when: dict[str, str] = {}
    for ev in history:
        try:
            label = DemandStatus(ev.to_stage).label
        except ValueError:
            continue
        when[label] = f"{ev.at.astimezone(zone) if zone else ev.at:%d %b}"
    skipped = (
        [] if demand.client_interview_required else [S.PANEL_SELECTED.label, S.PROFILES_WITH_CLIENT.label]
    )
    if demand.position_type == "Billable" and not demand.is_proactive_nb and S.STAFFED.label in when:
        when[BILLING_PENDING] = when.pop(S.STAFFED.label)  # the day the candidate joined
        if demand.billable_from:
            when[S.STAFFED.label] = f"{demand.billable_from:%d %b}"  # the day billing started
    return {"when": when, "skipped": skipped}


def detail(account: Account) -> dict[str, Any]:
    """The detailed workflow: for every step, who acts, what happens, what moves it on, where it can
    go back to and which escalation fires there. Thresholds and who is responsible come from the
    account's settings, so the page says what this account actually does."""
    cfg = account.settings
    back2, back5 = f"back to {number(S.SUBMITTED)}", f"back to {number(S.COVERAGE_REQUIRED)}"

    def trig(kind: EscalationType, when: str) -> list[str]:
        rule = cfg.rule_for(kind.value)
        if not rule.enabled:
            return []
        return [f"{kind.label} · {when} → {rule.responsible.label}"]

    days = lambda n: f"{n} working day{'' if n == 1 else 's'}"  # noqa: E731
    cut = f"{float(account.margin_threshold):g}%"
    steps: dict[DemandStatus, dict[str, Any]] = {
        S.DRAFT: {
            "who": ["own"],
            "does": "Fills the demand form: position, skills, bill rate, start date. One per position.",
            "next": "the owner submits it.",
        },
        S.SUBMITTED: {
            "who": ["gtd"],
            "does": "Mailed on submit and every morning until done. Creates the demand on GTD and links the "
            "requisition ID; the owner gets a completion mail.",
            "next": "the requisition ID is linked.",
            "trig": trig(EscalationType.NOT_SUBMITTED, "no ID by the next working day"),
        },
        S.RETURNED: {
            "who": ["own"],
            "does": "Sent back with the reason and what to fix.",
            "back": [f"Owner corrects and resubmits · {back2}"],
        },
        S.SENT_TO_GTD: {
            "who": ["staff"],
            "does": "GTD staffing checks and approves the requisition. The app cannot see GTD; the BCM sheet "
            "is the evidence.",
            "next": "the requisition appears in a BCM sheet.",
            "trig": trig(EscalationType.MISSING, f"after {days(account.grace_days)}"),
        },
        S.MISSING: {
            "who": ["gtd"],
            "does": "Checks GTD: link the right ID, or send it back to the owner.",
            "back": [f"Resubmitted · {back2}"],
        },
        S.LINKED: {
            "who": ["bcm"],
            "does": "In the sheet, with a status the account has not mapped to a stage yet.",
            "next": "the sheet shows a mapped status.",
            "trig": trig(EscalationType.UNLINKED_ROW, "a sheet row with no demand in the app"),
        },
        S.DROPPED: {
            "who": ["own"],
            "does": "Was in the last sheet, gone from this one. The owner confirms whether the position is "
            "still needed.",
            "back": [f"Still needed, resubmitted · {back2}"],
            "trig": trig(EscalationType.DROPPED, "at once"),
        },
        S.COVERAGE_REQUIRED: {
            "who": ["staff", "bcm"],
            "does": "Staffing sources across the supply channels. Candidates named on the sheet are added to "
            "the requisition; interviewers sharing a skill get a heads-up.",
            "next": "a candidate goes to the panel.",
            "trig": trig(EscalationType.AGING, f"{account.aging_days} days with no change"),
        },
        S.INCORRECT: {
            "who": ["own"],
            "does": "The sheet flags the demand as incorrect.",
            "back": [f"Owner corrects and resubmits · {back2}"],
            "trig": trig(EscalationType.INCORRECT, "at once"),
        },
        S.INTERVIEWING: {
            "who": ["pan", "gtd"],
            "does": "Staffing schedules outside the app; the GTD admin team records round and interviewer. "
            "The interviewer gets an invite and gives feedback: each area rated 1 to 10, then Offer, Reject "
            "or Hold.",
            "next": "a candidate is selected.",
            "back": [
                f"All candidates rejected · {back5}",
                f"Another round asked · owner approves, stays at {number(S.INTERVIEWING)}",
            ],
            "trig": trig(EscalationType.PANEL_SLA, f"{account.panel_timer_hours} hours")
            + trig(EscalationType.REJECTION_LIMIT, f"{account.rejection_limit} rejections"),
        },
        S.PANEL_SELECTED: {
            "who": ["own"],
            "does": "The owner is mailed that a candidate was selected.",
            "next": "the owner marks the client interview started, or the sheet says so.",
        },
        S.PROFILES_WITH_CLIENT: {
            "who": ["client", "own"],
            "does": "The client interviews the candidate. The owner records the client's decision, or the "
            "BCM sheet moves it on.",
            "next": "the client selects the candidate.",
            "back": [f"Client rejects · {back5}"],
        },
        S.OFFER_IN_PROCESS: {
            "who": ["own", "lead"],
            "does": "Margin = (bill rate − rate card cost for the practice and grade) ÷ bill rate, priced on "
            f"the offer date. At or above the cut-off ({cut}) the demand owner approves; below it leadership "
            "decides.",
            "next": "the offer is approved.",
            "back": [f"Declined, with a comment · {back5}"],
        },
        S.OFFER_IN_MARKET: {
            "who": ["staff", "own"],
            "does": "Staffing makes the offer. When it is accepted the owner records the expected joining "
            "date; the BCM sheet's date replaces it later.",
            "next": "the BCM sheet confirms the candidate joined.",
            "trig": trig(EscalationType.PAST_START, "the start date passes"),
        },
        S.STAFFED: {
            "who": ["bcm"],
            "does": "The candidate has joined and billing starts. Revenue loss stops counting. Shown for "
            f"{cfg.archive_after_days} days, then archived.",
        },
        S.CANCELLED: {"who": ["bcm"], "does": "The BCM sheet marks the requisition to be cancelled."},
        S.CLOSED: {
            "who": ["own", "gtd"],
            "does": 'An escalation was answered with "close the demand". Its other open escalations close '
            "with it.",
        },
    }
    by_label: dict[str, dict[str, Any]] = {s.label: d for s, d in steps.items()}
    ob = cfg.onboarding
    by_label[S.OFFER_IN_MARKET.label]["does"] += (
        f" A pre-joining checklist of {sum(c.enabled for c in ob.checklist)} items runs until the first day."
    )
    by_label[S.OFFER_IN_MARKET.label]["back"] = [f"Candidate will not join · {back5}"]
    by_label[S.OFFER_IN_MARKET.label]["trig"] += trig(
        EscalationType.PREJOIN_OVERDUE, "a checklist item passes its due date"
    )
    by_label[BILLING_PENDING] = {
        "who": ["own"],
        "does": "The candidate has joined. The owner confirms the first billable day: normally the joining "
        "day, or a later one with the reason billing waited. Until then the position still counts as open "
        "and revenue lost keeps counting.",
        "next": "the owner confirms the first billable day.",
        "trig": trig(EscalationType.NOT_BILLING, f"{days(ob.not_billing_after_days)} without confirmation"),
    }
    by_label[S.STAFFED.label] = {
        "who": ["own"],
        "does": "Joined and billing: the position is fulfilled. Shown for "
        f"{cfg.archive_after_days} days, then archived.",
    }
    return {
        "steps": by_label,
        "decision": {
            "after": S.INTERVIEWING.label,
            "q": "Does this demand need a client interview?",
            "yes": f"Continue to {number(S.PANEL_SELECTED)} · {S.PANEL_SELECTED.label}",
            "no": f"Skip to {number(S.OFFER_IN_PROCESS)} · {S.OFFER_IN_PROCESS.label}",
        },
        "who": {
            "own": ["Demand owner", "#0058AB", False],
            "gtd": ["GTD admin team", "#00AE9D", False],
            "pan": ["Interviewer", "#5685C6", False],
            "lead": ["Leadership", "#9F500D", False],
            "bcm": ["BCM sheet", "#43A063", False],
            "staff": ["GTD staffing", "#888F9A", True],
            "client": ["Client", "#888F9A", True],
        },
    }
