"""All tables. Import from here so Alembic and the app see the full metadata."""

from app.models.account import Account, BusinessUnit
from app.models.commercial import OfferApproval, RateCard
from app.models.demand import Demand
from app.models.escalation import Escalation, EscalationEvent
from app.models.gtd import GtdSubmission, NotificationBatch
from app.models.interview import Candidate, Interview
from app.models.sheet import ExcelImport, ExcelRow, StageEvent
from app.models.user import InterviewerProfile, User, UserAccount, UserBusinessUnit, UserPractice, member_of

__all__ = [
    "Account",
    "BusinessUnit",
    "Candidate",
    "Demand",
    "Escalation",
    "EscalationEvent",
    "ExcelImport",
    "ExcelRow",
    "GtdSubmission",
    "Interview",
    "InterviewerProfile",
    "NotificationBatch",
    "OfferApproval",
    "RateCard",
    "StageEvent",
    "User",
    "UserAccount",
    "member_of",
    "UserBusinessUnit",
    "UserPractice",
]
