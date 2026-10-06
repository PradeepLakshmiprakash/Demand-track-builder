"""All tables. Import from here so Alembic and the app see the full metadata."""

from app.models.account import Account, BusinessUnit
from app.models.admin_request import AdminRequest
from app.models.audit import AuditEntry
from app.models.commercial import OfferApproval, RateCard
from app.models.demand import Demand
from app.models.escalation import Escalation, EscalationEvent
from app.models.gtd import GtdSubmission, NotificationBatch
from app.models.interview import Candidate, Interview
from app.models.sheet import ExcelImport, ExcelRow, StageEvent
from app.models.user import InterviewerProfile, User, UserAccount, UserBusinessUnit, UserPractice, member_of

__all__ = [
    "AdminRequest",
    "AuditEntry",
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

# Every tracked change is recorded from here on, whoever makes it: core/audit.py starts listening when
# it is loaded.
import app.core.audit  # noqa: E402, F401
