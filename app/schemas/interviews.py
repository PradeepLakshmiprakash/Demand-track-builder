from datetime import UTC, date, datetime, time
from typing import Any

from app.services.interview_service import Feedback


def feedback_from_form(form: Any, dims: list[str], round_: str | None = None) -> Feedback:
    """The feedback form as posted (in the app, or from the no-sign-in link)."""
    ratings: dict[str, int] = {}
    bands: dict[str, str] = {}
    for i, area in enumerate(dims):
        raw = str(form.get(f"r{i}") or "")
        if raw.isdigit():
            ratings[area] = int(raw)
        band = str(form.get(f"b{i}") or "").strip()
        if band:
            bands[area] = band
    on = str(form.get("interviewed_on") or "")
    when = datetime.combine(date.fromisoformat(on), time(12, 0), UTC) if on else None
    outcome = str(form.get("outcome") or "")

    def text(key: str) -> str | None:
        return str(form.get(key) or "").strip() or None

    return Feedback(
        round=round_ or str(form.get("round") or ""),
        ratings=ratings,
        outcome=outcome,
        comments=str(form.get("comments") or ""),
        needs_next_round=form.get("needs_next_round") == "1" and outcome != "reject",
        next_round_note=str(form.get("next_round_note") or ""),
        interviewed_on=when,
        bands=bands,
        panel_bu=text("panel_bu"),
        panel_designation=text("panel_designation"),
        panel_practice=text("panel_practice"),
        support_needed=text("support_needed") if outcome != "select" else None,
        support_area=text("support_area") if outcome != "select" else None,
        designation=text("designation"),
        other_role_fit=text("other_role_fit"),
        other_role_capacity=text("other_role_capacity") if text("other_role_fit") == "Yes" else None,
    )


def feedback_values(fb: Feedback) -> dict[str, Any]:
    """What was typed, to show the form again after an error."""
    return {
        "round": fb.round,
        "ratings": fb.ratings,
        "bands": fb.bands or {},
        "outcome": fb.outcome,
        "comments": fb.comments,
        "needs_next_round": fb.needs_next_round,
        "next_round_note": fb.next_round_note,
        "panel_bu": fb.panel_bu,
        "panel_designation": fb.panel_designation,
        "panel_practice": fb.panel_practice,
        "support_needed": fb.support_needed,
        "support_area": fb.support_area,
        "designation": fb.designation,
        "other_role_fit": fb.other_role_fit,
        "other_role_capacity": fb.other_role_capacity,
    }
