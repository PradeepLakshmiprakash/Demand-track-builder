"""Sample requests to the Administrator, from every role, for the dummy Discover NA account.

They tell the app's story of who asks for what: the Lead admin (a member of the GTD admin team) holds
special access because they asked the Administrator for it; demand owners, the GTD admin team,
leadership and interviewers each ask for the things their own work needs.

Kept out of seed.load so tests start with no requests. `python -m seed` loads these too; to add them to
a database that is already seeded, run `python -m seed.requests_demo`.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, AdminRequest, User

ADMINISTRATOR = "anil.v@example.com"

# requester's email, kind, what they asked, status, the Administrator's note, days ago raised
REQUESTS: list[tuple[str, str, str, str, str | None, int]] = [
    (
        "kavya.r@example.com", "special",
        "Lead admin access to see client bill rates, cost rates and margins on every demand in the account.",
        "done", "Granted: rates are visible to the Lead admin on all demands.", 40,
    ),
    (
        "kavya.r@example.com", "special",
        "Lead admin access to set the agreed non-billable cap for each business unit.",
        "done", "Granted: caps are set from the Account overview.", 38,
    ),
    (
        "kavya.r@example.com", "special",
        "Lead admin access to manage interviewer profiles (skills, practices, grade limit, availability).",
        "done", "Granted: Interviewer profiles is on the Lead admin's menu.", 36,
    ),
    (
        "kavya.r@example.com", "special",
        "Lead admin access to raise an offer approval by hand when the BCM sheet has no candidate name.",
        "done", "Granted.", 35,
    ),
    (
        "kavya.r@example.com", "special",
        "Lead admin access to see every request raised to the Administrator in this account.",
        "done", "Granted: the Lead admin sees all requests; everyone else sees their own.", 34,
    ),
    (
        "farah.q@example.com", "settings",
        "The BCM sheet has a new status 'On Hold - Client'. Please map it to a stage so the import stops "
        "reporting it as unmapped.",
        "done", "Mapped to Resourcing In Progress · Sourcing profiles.", 12,
    ),
    (
        "vikram.p@example.com", "profile",
        "Add Kafka and Spark to my interview skills, and raise my grade limit to C2.",
        "done", "Skills added. Grade limit raised to C2 as confirmed by the Lead admin.", 9,
    ),
    (
        "neha.t@example.com", "special",
        "Read access to the BANKING demands, which I cover while Meera S. is on leave until 20 Oct.",
        "declined", "Cover is arranged through the Lead admin; ask her to reassign the demands.", 7,
    ),
    (
        "kavya.r@example.com", "rate_card",
        "C2 in DCX-FS goes to $73.00 an hour from 1 Nov 2026, per the revised cost sheet from Finance.",
        "open", None, 3,
    ),
    (
        "priya.n@example.com", "data_fix",
        "The client bill rate on DM-000121 should be $98.00, not $95.00: the client confirmed the revised "
        "rate on 28 Sep.",
        "open", None, 2,
    ),
    (
        "sanjay.m@example.com", "escalation",
        "Inform leadership of overdue escalations for high severity only; medium and low can stay with the "
        "delivery head.",
        "open", None, 1,
    ),
    (
        "anita.g@example.com", "access",
        "I have moved to the DMN-FS practice: please update my practice so I get the right interviews.",
        "open", None, 0,
    ),
]  # fmt: skip


def load_requests(db: Session, account_name: str = "Discover NA", now: datetime | None = None) -> int:
    """Add the sample requests to the account, unless it already has some. Returns how many were added."""
    now = now or datetime.now(UTC)
    account = db.scalars(select(Account).where(Account.name == account_name)).first()
    if account is None or db.scalar(select(AdminRequest.id).where(AdminRequest.account_id == account.id)):
        return 0
    people = {u.email: u for u in db.scalars(select(User))}
    handler = people.get(ADMINISTRATOR)
    n = 0
    for email, kind, details, status, note, days in REQUESTS:
        who = people.get(email)
        if who is None:
            continue
        raised = now - timedelta(days=days, hours=3)
        handled = status != "open" and handler is not None
        db.add(
            AdminRequest(
                account_id=account.id,
                kind=kind,
                details=details,
                status=status,
                note=note,
                requested_by=who.id,
                created_at=raised,
                handled_by=handler.id if handled and handler else None,
                handled_at=raised + timedelta(hours=20) if handled else None,
            )  # fmt: skip
        )
        n += 1
    db.commit()
    return n


if __name__ == "__main__":
    from app.core.db import new_session

    with new_session() as session:
        print(f"Added {load_requests(session)} sample requests.")
