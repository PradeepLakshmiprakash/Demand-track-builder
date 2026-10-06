"""Interviews (flow-artifact §7, re-centred by the answers of 24 Sep; decisions.md "Answers before Phase 6").

Staffing schedules interviews outside the app and CVs arrive by email, so the app's job is:

1. Know which requisition each candidate belongs to. Candidates come from the BCM sheet (the
   Candidate Name cell on the requisition's row) or are added by hand; one without a requisition
   waits for the GTD admin team or the panelist to map it.
2. Capture the panelist's recommendation: ratings, select / reject / hold, comments, and optionally
   "needs another round". The panelist finds the candidate by name; the app shows the requisitions
   that name is on. A recommendation can be recorded before scheduling is known.
3. An L2 asked for by the panelist waits for the demand owner's approval, then for staffing to
   schedule it and for someone to be assigned (who interviews at L2 is still open, so it's a
   placeholder the GTD admin team fills).
4. When an interviewer is assigned, they get an invite with both IDs, the JD and CV links and a
   single-use feedback link that works without signing in.
5. Interviewers sharing at least one technology with a newly linked requisition get a heads-up.
6. Karat (a separate interview platform) posts results through `record_external`; see the stub API.
"""

import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from zoneinfo import ZoneInfo

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core import feedback_reference, mail, storage
from app.core.config import get_settings
from app.core.enums import (
    FINISHED,
    CandidateSource,
    DemandStatus,
    InterviewOutcome,
    InterviewSource,
    InterviewStatus,
    Role,
)
from app.core.security import Actor
from app.models import (
    Account,
    BusinessUnit,
    Candidate,
    Demand,
    Interview,
    InterviewerProfile,
    User,
    member_of,
)

CV_EXTENSIONS = {".pdf", ".doc", ".docx", ".rtf", ".txt"}
ROUNDS = ("L1", "L2")
# Stages where interviews happen: the demand is being covered.
INTERVIEW_STAGES = frozenset(
    {
        DemandStatus.LINKED,
        DemandStatus.COVERAGE_REQUIRED,
        DemandStatus.INTERVIEWING,
        DemandStatus.PANEL_SELECTED,
        DemandStatus.PROFILES_WITH_CLIENT,
        DemandStatus.OFFER_IN_PROCESS,
    }
)
SEARCH_MIN = 70


class InterviewError(ValueError):
    pass


def _progress(db: Session, demand_id: int | None, actor_id: int | None) -> None:
    """Let the demand's stage follow its panel records (pipeline_service)."""
    from app.services import pipeline_service  # it imports margin_service, which imports this module

    pipeline_service.apply_for(db, demand_id, actor_id)


# --- Candidates ------------------------------------------------------------------------------------


def split_names(cell: str | None) -> list[str]:
    """The BCM sheet's Candidate Name cell can hold several people, one per line."""
    names = []
    for line in (cell or "").splitlines():
        name = re.sub(r"^\s*(\d+[.)]|[-•*])\s*", "", line).strip()
        if name and name not in ("0", "-") and not name.isdigit():
            names.append(name[:160])
    return list(dict.fromkeys(names))


def ensure_candidate(
    db: Session,
    account_id: int,
    demand: Demand | None,
    name: str,
    *,
    channel: str | None = None,
    source: CandidateSource = CandidateSource.SHEET,
) -> Candidate:
    """The candidate with this name on this requisition (or unmapped), created if new."""
    name = name.strip()[:160]
    stmt = select(Candidate).where(Candidate.account_id == account_id)
    stmt = (
        stmt.where(Candidate.demand_id == demand.id) if demand else stmt.where(Candidate.demand_id.is_(None))
    )
    for c in db.scalars(stmt):
        if c.name.casefold() == name.casefold():
            if channel and not c.channel:
                c.channel = channel
            return c
    c = Candidate(
        account_id=account_id,
        demand_id=demand.id if demand else None,
        name=name,
        channel=channel,
        source=source.value,
    )
    db.add(c)
    db.flush()
    return c


def candidates_from_sheet(
    db: Session, account_id: int, demand: Demand, cell: str | None, channel: str | None
) -> list[Candidate]:
    """Candidates named on the requisition's BCM sheet row, so each is known to be on that requisition."""
    return [ensure_candidate(db, account_id, demand, n, channel=channel) for n in split_names(cell)]


def stage_from_sheet(db: Session, account_id: int, demand: Demand, cell: str | None, label: str) -> None:
    """Candidates named on the row share its stage (Profiles with client, Offer in market, Staffed)."""
    for c in candidates_from_sheet(db, account_id, demand, cell, None):
        c.current_stage = label


@dataclass
class Match:
    candidate: Candidate
    demand: Demand | None
    score: int


def search(db: Session, account_id: int, query: str, limit: int = 12) -> list[Match]:
    """Candidates whose name is like the query, with the requisition each is on."""
    q = query.strip().casefold()
    if len(q) < 2:
        return []
    rows = db.execute(
        select(Candidate, Demand)
        .outerjoin(Demand, Demand.id == Candidate.demand_id)
        .where(Candidate.account_id == account_id)
        .options(selectinload(Demand.submissions))
    ).all()
    out = []
    for c, d in rows:
        score = max(fuzz.WRatio(q, c.name.casefold()), fuzz.token_sort_ratio(q, c.name.casefold()))
        if score >= SEARCH_MIN:
            out.append(Match(c, d, round(score)))
    out.sort(key=lambda m: (-m.score, m.candidate.name, m.demand.app_ref if m.demand else ""))
    return out[:limit]


def technologies(demands: list[Demand]) -> list[str]:
    """Every technology named on these requisitions (primary or secondary), once each, A to Z."""
    seen: dict[str, str] = {}
    for d in demands:
        for t in [*(d.primary_skills or []), *(d.secondary_skills or [])]:
            if t.strip():
                seen.setdefault(t.strip().casefold(), t.strip())
    return sorted(seen.values(), key=str.casefold)


def with_technology(demands: list[Demand], tech: str) -> list[Demand]:
    want = tech.strip().casefold()
    if not want:
        return demands
    return [
        d
        for d in demands
        if want in {t.strip().casefold() for t in [*(d.primary_skills or []), *(d.secondary_skills or [])]}
    ]


def open_requisitions(db: Session, account_id: int) -> list[Demand]:
    return list(
        db.scalars(
            select(Demand)
            .where(Demand.account_id == account_id, Demand.status.in_([s.value for s in INTERVIEW_STAGES]))
            .options(selectinload(Demand.submissions))
            .order_by(Demand.app_ref)
        )
    )


def map_candidate(db: Session, actor: Actor, candidate_id: int, demand_id: int) -> Candidate:
    """Put an unmapped candidate (and their interviews) on a requisition."""
    c = _candidate(db, actor, candidate_id)
    d = _demand(db, actor, demand_id)
    if c.demand_id == d.id:
        return c
    if c.demand_id is not None:
        raise InterviewError(f"{c.name} is already on another requisition. Add them to this one instead.")
    clash = next(
        (
            x
            for x in db.scalars(select(Candidate).where(Candidate.demand_id == d.id))
            if x.name.casefold() == c.name.casefold()
        ),
        None,
    )
    if clash is not None:
        # Same person already on that requisition: fold this record into it.
        for iv in db.scalars(select(Interview).where(Interview.candidate_id == c.id)):
            iv.candidate_id, iv.demand_id = clash.id, d.id
        clash.cv_path = clash.cv_path or c.cv_path
        db.delete(c)
        db.flush()
        _progress(db, d.id, actor.id)
        db.commit()
        return clash
    c.demand_id = d.id
    for iv in db.scalars(select(Interview).where(Interview.candidate_id == c.id)):
        iv.demand_id = d.id
    db.flush()
    _progress(db, d.id, actor.id)
    db.commit()
    return c


def add_candidate(db: Session, actor: Actor, name: str, demand_id: int | None) -> Candidate:
    if not name.strip():
        raise InterviewError("Enter the candidate's name.")
    d = _demand(db, actor, demand_id) if demand_id else None
    c = ensure_candidate(db, actor.account_id, d, name, source=CandidateSource.MANUAL)
    db.commit()
    return c


def attach_cv(db: Session, actor: Actor, candidate_id: int, filename: str, data: bytes) -> Candidate:
    c = _candidate(db, actor, candidate_id)
    try:
        c.cv_path = storage.save(f"cv/{c.id}", filename, data, CV_EXTENSIONS)
    except storage.StorageError as e:
        raise InterviewError(f"CV not saved: {e}.") from e
    db.commit()
    return c


# --- Recommendations -------------------------------------------------------------------------------


@dataclass
class Feedback:
    round: str
    ratings: dict[str, int]
    outcome: str
    comments: str | None
    needs_next_round: bool = False
    next_round_note: str | None = None
    interviewed_on: datetime | None = None
    # The fuller form (as in Acquisition Central). None for `bands` means the caller didn't use the form
    # (a test, or an integration), so bands aren't asked for.
    bands: dict[str, str] | None = None
    panel_bu: str | None = None
    panel_designation: str | None = None
    panel_practice: str | None = None
    support_needed: str | None = None
    support_area: str | None = None
    designation: str | None = None  # the grade the panelist would put the candidate at
    other_role_fit: str | None = None  # "Yes" / "No": fit for another role in the firm
    other_role_capacity: str | None = None

    def details(self, areas: list[str]) -> dict[str, object]:
        bands = self.bands or {}
        rated = [self.ratings[a] for a in areas if a in self.ratings]
        return {
            "overall": feedback_reference.overall(rated),
            "bands": {a: bands[a] for a in areas if bands.get(a)},
            "sentences": {
                a: s for a in areas if bands.get(a) and (s := feedback_reference.sentence(a, bands[a]))
            },
            "panel_bu": self.panel_bu or None,
            "panel_designation": self.panel_designation or None,
            "panel_practice": self.panel_practice or None,
            "support_needed": self.support_needed or None,
            "support_area": self.support_area or None,
            "designation": self.designation or None,
            "other_role_fit": self.other_role_fit or None,
            "other_role_capacity": (self.other_role_capacity or "").strip() or None,
        }


def form_reference(db: Session, account: Account) -> dict[str, object]:
    """What the feedback form offers: the rated areas with their bands, and the lists it picks from."""
    cfg = account.settings
    return {
        "areas": feedback_reference.form_reference(cfg.interview_ratings),
        "grades": list(cfg.grades),
        "practices": list(cfg.practices),
        "business_units": list(
            db.scalars(
                select(BusinessUnit.name)
                .where(BusinessUnit.account_id == account.id)
                .order_by(BusinessUnit.name)
            )
        ),
        "support_options": feedback_reference.SUPPORT_OPTIONS,
    }


def _check_feedback(account: Account, fb: Feedback) -> None:
    if fb.round not in ROUNDS:
        raise InterviewError("Choose the round.")
    try:
        InterviewOutcome(fb.outcome)
    except ValueError as e:
        raise InterviewError("Choose offer, reject or hold.") from e
    cfg = account.settings
    dims = cfg.interview_ratings
    missing = [d for d in dims if fb.ratings.get(d) not in feedback_reference.SCALE]
    if missing:
        raise InterviewError("Rate 1 to 10: " + ", ".join(missing) + ".")
    if fb.bands is not None:
        unbanded = [d for d in dims if feedback_reference.sentence(d, fb.bands.get(d, "")) is None]
        if unbanded:
            raise InterviewError("Choose a band for: " + ", ".join(unbanded) + ".")
    declined = fb.outcome in (InterviewOutcome.REJECT, InterviewOutcome.HOLD)
    if declined and not (fb.support_needed or "").strip() and not (fb.comments or "").strip():
        raise InterviewError(
            "Say where the candidate needs support, or add a remark: what was missing, or what the hold "
            "is waiting on."
        )
    if fb.support_area and fb.support_area not in dims:
        raise InterviewError("Choose the area the support is needed in from the rated areas.")
    if fb.designation and fb.designation not in cfg.grades:
        raise InterviewError("Choose the recommended designation from the account's grades.")
    if fb.other_role_fit and fb.other_role_fit not in ("Yes", "No"):
        raise InterviewError("Fit for another role is Yes or No.")
    if fb.needs_next_round and fb.outcome == InterviewOutcome.REJECT:
        raise InterviewError("A rejected candidate can't go to another round.")
    if fb.needs_next_round and fb.round == ROUNDS[-1]:
        raise InterviewError(f"{fb.round} is the last round.")


def record_feedback(
    db: Session,
    account: Account,
    interviewer_id: int,
    candidate: Candidate,
    fb: Feedback,
    interview: Interview | None = None,
) -> Interview:
    """Save a panelist's recommendation, on a scheduled interview or as a new record."""
    _check_feedback(account, fb)
    now = datetime.now(UTC)
    if interview is None:
        interview = Interview(
            demand_id=candidate.demand_id,
            candidate_id=candidate.id,
            round=fb.round,
            source=InterviewSource.APP.value,
            interviewer_id=interviewer_id,
            scheduled_at=fb.interviewed_on,
        )
        db.add(interview)
    elif interview.status_enum is InterviewStatus.COMPLETED:
        raise InterviewError("Feedback for this interview is already in.")
    interview.ratings = dict(fb.ratings)
    interview.feedback_details = fb.details(account.settings.interview_ratings)
    interview.outcome, interview.comments = fb.outcome, (fb.comments or "").strip() or None
    interview.submitted_at, interview.status = now, InterviewStatus.COMPLETED.value
    interview.interviewer_id = interview.interviewer_id or interviewer_id
    interview.needs_next_round = fb.needs_next_round
    interview.feedback_token = None  # single use
    candidate.current_stage = f"{fb.round} {InterviewOutcome(fb.outcome).value}"
    db.flush()
    if fb.needs_next_round:
        request_next_round(db, account, interview, interviewer_id, fb.next_round_note)
    _progress(db, interview.demand_id, interviewer_id)
    db.commit()
    return interview


def request_next_round(
    db: Session, account: Account, done: Interview, requested_by: int, note: str | None
) -> Interview:
    nxt = ROUNDS[ROUNDS.index(done.round) + 1]
    existing = db.scalar(
        select(Interview).where(
            Interview.candidate_id == done.candidate_id,
            Interview.round == nxt,
            Interview.status != InterviewStatus.DECLINED.value,
        )
    )
    if existing is not None:
        return existing
    req = Interview(
        demand_id=done.demand_id,
        candidate_id=done.candidate_id,
        round=nxt,
        status=InterviewStatus.REQUESTED.value,
        requested_by=requested_by,
        request_note=(note or "").strip() or None,
    )
    db.add(req)
    db.flush()
    _mail_owner_about_request(db, account, req)
    return req


def decide_next_round(
    db: Session, actor: Actor, interview_id: int, approve: bool, note: str | None
) -> Interview:
    """The demand owner (or the lead admin) approves or declines an asked-for round."""
    iv = _interview(db, actor, interview_id)
    if iv.status_enum is not InterviewStatus.REQUESTED:
        raise InterviewError("That round isn't waiting for a decision.")
    if iv.demand_id is None:
        raise InterviewError("Map the candidate to a requisition first; its owner decides.")
    d = db.get_one(Demand, iv.demand_id)
    if not (d.owner_id == actor.id or actor.role is Role.ADMIN):
        raise InterviewError("The requisition's demand owner decides on another round.")
    if not approve and not (note or "").strip():
        raise InterviewError("Add a note: why no further round.")
    iv.status = (InterviewStatus.OPEN if approve else InterviewStatus.DECLINED).value
    iv.decided_by, iv.decided_at, iv.decision_note = actor.id, datetime.now(UTC), (note or "").strip() or None
    db.flush()
    _progress(db, iv.demand_id, actor.id)
    db.commit()
    return iv


# --- Scheduling placeholders -----------------------------------------------------------------------


def schedule(
    db: Session,
    actor: Actor,
    *,
    candidate_id: int,
    round_: str,
    interviewer_id: int | None,
    when: datetime | None,
    interview_id: int | None = None,
) -> Interview:
    """Record what's known of an interview staffing arranged. With an interviewer it's Scheduled and
    they get the invite; without one it stays To be scheduled."""
    c = _candidate(db, actor, candidate_id)
    if round_ not in ROUNDS:
        raise InterviewError("Choose the round.")
    iv = _interview(db, actor, interview_id) if interview_id else None
    if iv is not None and iv.status_enum not in (InterviewStatus.OPEN, InterviewStatus.SCHEDULED):
        raise InterviewError("Only an approved or scheduled round can be (re)scheduled.")
    if iv is None:
        iv = Interview(
            demand_id=c.demand_id, candidate_id=c.id, round=round_, status=InterviewStatus.OPEN.value
        )
        db.add(iv)
    iv.round, iv.scheduled_at = round_, when
    newly_assigned = False
    if interviewer_id:
        who = db.scalar(
            select(User).where(
                User.id == interviewer_id,
                member_of(actor.account_id, Role.INTERVIEWER),
            )
        )
        if who is None:
            raise InterviewError("Pick an active interviewer.")
        newly_assigned = iv.interviewer_id != who.id or iv.feedback_token is None
        iv.interviewer_id, iv.status = who.id, InterviewStatus.SCHEDULED.value
        if newly_assigned:
            iv.feedback_token = secrets.token_urlsafe(32)
    db.flush()
    if newly_assigned:
        _send_invite(db, db.get_one(Account, actor.account_id), iv)
    _progress(db, iv.demand_id, actor.id)
    db.commit()
    return iv


def feedback_link(iv: Interview) -> str | None:
    return f"{get_settings().app_base_url}/feedback/{iv.feedback_token}" if iv.feedback_token else None


def by_token(db: Session, token: str) -> Interview | None:
    if not token or len(token) < 20:
        return None
    return db.scalar(select(Interview).where(Interview.feedback_token == token))


# --- Mail ------------------------------------------------------------------------------------------


def _subject_ids(db: Session, iv: Interview) -> tuple[str, str]:
    d = db.get(Demand, iv.demand_id) if iv.demand_id else None
    return (d.gtd_req_id or "no GTD ID" if d else "requisition to confirm"), (d.app_ref if d else "unmapped")


def _send_invite(db: Session, account: Account, iv: Interview) -> None:
    who = db.get_one(User, iv.interviewer_id)
    c = db.get_one(Candidate, iv.candidate_id)
    d = db.get(Demand, iv.demand_id) if iv.demand_id else None
    req, ref = _subject_ids(db, iv)
    base = get_settings().app_base_url
    tz = ZoneInfo(account.settings.timezone)
    when = (
        iv.scheduled_at.astimezone(tz).strftime("%a %d %b %Y, %H:%M %Z")
        if iv.scheduled_at
        else "time to be confirmed by staffing"
    )
    lines = [
        f"You're on the {iv.round} panel for {c.name}.",
        f"Requisition: {req} · {ref}" + (f" · {d.name} · {d.grade}" if d else ""),
        f"When: {when}",
        f"Required skills: {', '.join(d.primary_skills)}" if d and d.primary_skills else "",
        f"Job description: {base}/demands/{d.app_ref}/jd" if d and d.jd_path else "",
        f"Candidate CV: {base}/cv/{iv.feedback_token}" if c.cv_path else "CV: not attached yet",
        "",
        f"Your feedback (no sign-in needed, one use): {feedback_link(iv)}",
    ]
    text = "\n".join(x for x in lines if x is not None)
    html = "<p style='font:15px sans-serif'>" + "<br>".join(escape(x) for x in lines if x) + "</p>"
    html = html.replace(escape(feedback_link(iv) or ""), f"<a href='{feedback_link(iv)}'>Give feedback</a>")
    mail.notify(
        mail.Mail(to=[who.email], subject=f"[{req} | {ref}] {iv.round} – {c.name}", text=text, html=html)
    )


def _mail_owner_about_request(db: Session, account: Account, req: Interview) -> None:
    if req.demand_id is None:
        return
    d = db.get_one(Demand, req.demand_id)
    owner = db.get_one(User, d.owner_id)
    c = db.get_one(Candidate, req.candidate_id)
    asker = db.get(User, req.requested_by) if req.requested_by else None
    link = f"{get_settings().app_base_url}/demands/{d.app_ref}"
    text = (
        f"{asker.name if asker else 'The panelist'} asked for an {req.round} round for {c.name} "
        f"on {d.gtd_req_id or d.app_ref} ({d.name}).\n"
        + (f"Why: {req.request_note}\n" if req.request_note else "")
        + f"Approve or decline: {link}"
    )
    mail.notify(
        mail.Mail(
            to=[owner.email],
            subject=f"[{d.gtd_req_id or d.app_ref} | {d.app_ref}] {req.round} requested for {c.name}",
            text=text,
        )
    )


# --- Heads-up alerts -------------------------------------------------------------------------------


def _skills(d: Demand) -> set[str]:
    return {s.casefold() for s in (d.primary_skills or []) + (d.secondary_skills or [])}


def matching_interviewers(db: Session, account_id: int, d: Demand) -> list[User]:
    """Interviewers sharing at least one technology with the requisition (confirmed 24 Sep)."""
    wanted = _skills(d)
    if not wanted:
        return []
    rows = db.execute(
        select(User, InterviewerProfile)
        .join(InterviewerProfile, InterviewerProfile.user_id == User.id)
        .where(User.active, InterviewerProfile.active, User.accounts.any(id=account_id))
    ).all()
    return [u for u, p in rows if wanted & {s.casefold() for s in p.skills}]


def alert_interviewers(db: Session, account: Account, demand_ids: list[int]) -> int:
    """Once per requisition: tell matching interviewers a new requisition in their skills is linked."""
    pending = [
        d
        for d in db.scalars(
            select(Demand).where(Demand.id.in_(demand_ids)).options(selectinload(Demand.submissions))
        )
        if d.interviewers_alerted_at is None and d.status_enum in INTERVIEW_STAGES
    ]
    per_person: dict[int, tuple[User, list[Demand]]] = {}
    for d in pending:
        for u in matching_interviewers(db, account.id, d):
            per_person.setdefault(u.id, (u, []))[1].append(d)
    for u, ds in per_person.values():
        lines = [
            f"  {d.gtd_req_id or d.app_ref} · {d.name} · {d.grade} · {', '.join(d.primary_skills)}"
            for d in ds
        ]
        plural = "s" if len(ds) > 1 else ""
        mail.send(
            mail.Mail(
                to=[u.email],
                subject=f"[Demand Tracker] {len(ds)} new requisition{plural} in your skills",
                text="Profiles expected soon. You'll get an invite when you're assigned an interview.\n\n"
                + "\n".join(lines)
                + f"\n\n{get_settings().app_base_url}/interviews",
            )
        )
    now = datetime.now(UTC)
    for d in pending:
        d.interviewers_alerted_at = now
    db.flush()
    return len(per_person)


def new_in_my_skills(db: Session, actor: Actor) -> list[Demand]:
    prof = db.get(InterviewerProfile, actor.id)
    if prof is None or not prof.skills:
        return []
    mine = {s.casefold() for s in prof.skills}
    return [d for d in open_requisitions(db, actor.account_id) if mine & _skills(d)]


# --- Karat (external interview platform) ---------------------------------------------------------


def record_external(
    db: Session,
    account_id: int,
    *,
    candidate_name: str,
    gtd_req_id: str | None,
    round_: str,
    outcome: str,
    external_ref: str,
    report_url: str | None,
    comments: str | None = None,
) -> Interview:
    """A result from Karat. The candidate is matched to a requisition by GTD ID when one is sent,
    otherwise it waits unmapped for the GTD admin team. Re-sending the same result is a no-op."""
    same = db.scalar(
        select(Interview).where(
            Interview.external_ref == external_ref, Interview.source == InterviewSource.KARAT.value
        )
    )
    if same is not None:
        return same
    try:
        InterviewOutcome(outcome)
    except ValueError as e:
        raise InterviewError("outcome must be select, reject or hold") from e
    if round_ not in ROUNDS:
        raise InterviewError("round must be L1 or L2")
    d = None
    if gtd_req_id:
        from app.models import GtdSubmission

        d = db.scalar(
            select(Demand)
            .join(GtdSubmission)
            .where(Demand.account_id == account_id, GtdSubmission.gtd_req_id == gtd_req_id.upper())
        )
    c = ensure_candidate(db, account_id, d, candidate_name, source=CandidateSource.KARAT)
    iv = Interview(
        demand_id=c.demand_id,
        candidate_id=c.id,
        round=round_,
        status=InterviewStatus.COMPLETED.value,
        source=InterviewSource.KARAT.value,
        outcome=outcome,
        comments=comments,
        submitted_at=datetime.now(UTC),
        external_ref=external_ref[:80],
        report_url=report_url,
    )
    db.add(iv)
    c.current_stage = f"{round_} {outcome}"
    db.flush()
    _progress(db, iv.demand_id, None)
    db.commit()
    return iv


# --- Views -----------------------------------------------------------------------------------------


def for_candidate(db: Session, candidate_id: int) -> list[Interview]:
    return list(
        db.scalars(select(Interview).where(Interview.candidate_id == candidate_id).order_by(Interview.id))
    )


def for_demand(db: Session, demand_id: int) -> list[tuple[Candidate, list[Interview]]]:
    cands = list(
        db.scalars(select(Candidate).where(Candidate.demand_id == demand_id).order_by(Candidate.name))
    )
    return [(c, for_candidate(db, c.id)) for c in cands]


def assigned_to(db: Session, actor: Actor) -> list[tuple[Interview, Candidate, Demand | None]]:
    rows = db.execute(
        select(Interview, Candidate, Demand)
        .join(Candidate, Candidate.id == Interview.candidate_id)
        .outerjoin(Demand, Demand.id == Interview.demand_id)
        .where(Interview.interviewer_id == actor.id, Candidate.account_id == actor.account_id)
        .order_by(Interview.status.desc(), Interview.scheduled_at.nulls_last(), Interview.id.desc())
    ).all()
    return [(i, c, d) for i, c, d in rows]


def rejections(db: Session, demand_id: int) -> int:
    return len(
        list(
            db.scalars(
                select(Interview.id).where(
                    Interview.demand_id == demand_id, Interview.outcome == InterviewOutcome.REJECT.value
                )
            )
        )
    )


# --- Guards ----------------------------------------------------------------------------------------


def _candidate(db: Session, actor: Actor, candidate_id: int) -> Candidate:
    c = db.get(Candidate, candidate_id)
    if c is None or c.account_id != actor.account_id:
        raise InterviewError("Candidate not found.")
    return c


def _demand(db: Session, actor: Actor, demand_id: int | None) -> Demand:
    d = db.get(Demand, demand_id) if demand_id else None
    if d is None or d.account_id != actor.account_id:
        raise InterviewError("Requisition not found.")
    if d.status_enum in FINISHED:
        raise InterviewError(f"{d.app_ref} is {d.status_enum.label.lower()}.")
    return d


def _interview(db: Session, actor: Actor, interview_id: int | None) -> Interview:
    iv = db.get(Interview, interview_id) if interview_id else None
    if iv is None:
        raise InterviewError("Interview not found.")
    _candidate(db, actor, iv.candidate_id)
    return iv
