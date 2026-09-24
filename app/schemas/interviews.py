from datetime import UTC, date, datetime, time
from typing import Any

from app.services.interview_service import Feedback


def feedback_from_form(form: Any, dims: list[str], round_: str | None = None) -> Feedback:
    """The feedback form as posted (in the app, or from the no-sign-in link)."""
    ratings: dict[str, int] = {}
    for i, _ in enumerate(dims):
        raw = str(form.get(f"r{i}") or "")
        if raw.isdigit():
            ratings[dims[i]] = int(raw)
    on = str(form.get("interviewed_on") or "")
    when = datetime.combine(date.fromisoformat(on), time(12, 0), UTC) if on else None
    return Feedback(
        round=round_ or str(form.get("round") or ""),
        ratings=ratings,
        outcome=str(form.get("outcome") or ""),
        comments=str(form.get("comments") or ""),
        needs_next_round=form.get("needs_next_round") == "1",
        next_round_note=str(form.get("next_round_note") or ""),
        interviewed_on=when,
    )
