"""Reconciliation: what the DP sheet says about every demand sent to GTD (flow-artifact §5).

After submission the app can't see GTD; the DP sheet is the only evidence. For each import:

1. Match rows to demands, stopping at the first hit:
   tier 1  the row's requisition ID is linked to a demand;
   tier 2  the row's name carries [DM-xxxxxx] and that demand has no ID yet → linked automatically;
   tier 3  fuzzy suggestions (name, originator, practice, grade, region, start ±7 days) that a person
           confirms. Identical bulk demands are offered as interchangeable slots, in ref order.
2. Matched rows set the demand's stage through the account's status mapping. "In Correct Demnad"
   (or whatever the mapping sends to Incorrect) opens an escalation.
3. Sent to GTD but not in the sheet: within the grace period (working days since the ID was linked)
   it's awaiting GTD; after it, missing + escalation.
4. In the previous import but not this one, and still open: dropped + escalation. Staffed, cancelled
   and closed demands can leave the sheet quietly.

Only the latest import is reconciled or acted on; older imports are history. Re-running is safe:
stage changes and escalations are no-ops when nothing changed, and people's confirmations are kept.
"""

import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.account_config import AccountConfig
from app.core.enums import (
    FINISHED,
    DemandStatus,
    EscalationType,
    Role,
    RowOutcome,
    StageOrigin,
)
from app.core.workdays import add_working_days
from app.models import (
    Account,
    BusinessUnit,
    Demand,
    ExcelImport,
    ExcelRow,
    GtdSubmission,
    StageEvent,
    User,
    member_of,
)
from app.services import interview_service, margin_service, pipeline_service
from app.services.demand_service import record_stage
from app.services.escalation_service import open_escalation

log = logging.getLogger("demand_tracker.reconcile")
PREFIX = re.compile(r"\[(DM-\d{6})\]")
LINKABLE = (DemandStatus.SUBMITTED, DemandStatus.NOTIFIED)  # tier-2/3 targets without an ID
EXPECTED = (DemandStatus.SENT_TO_GTD, DemandStatus.MISSING)  # has an ID, not yet seen in a sheet
# Stages the sheet sets for the people it names (an offer's own stage comes from its approval).
SHEET_CANDIDATE_STAGES = (
    DemandStatus.PROFILES_WITH_CLIENT,
    DemandStatus.OFFER_IN_MARKET,
    DemandStatus.STAFFED,
)
# Back with the owner or the GTD admin team for a new GTD entry: the old ID's rows wait for the new one.
RESUBMITTING = (DemandStatus.RETURNED, DemandStatus.SUBMITTED, DemandStatus.NOTIFIED)
SUGGEST_MIN = 60
SUGGEST_MAX = 3


class ReconcileError(ValueError):
    pass


@dataclass
class Summary:
    rows: int = 0
    sent: int = 0  # demands expected in the sheet: in it, awaiting, missing or dropped
    in_sheet: int = 0
    matched: int = 0
    prefix: list[str] = field(default_factory=list)
    confirmed: int = 0
    needs_person: int = 0
    duplicates: int = 0
    invalid: int = 0
    awaiting: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    incorrect: list[str] = field(default_factory=list)
    stage_changes: list[dict[str, str]] = field(default_factory=list)
    unmapped_statuses: list[str] = field(default_factory=list)
    new_escalations: list[str] = field(default_factory=list)
    offers: list[str] = field(default_factory=list)  # new offer approvals raised from the sheet
    superseded: list[str] = field(default_factory=list)  # rows for replaced requisition IDs, ignored
    resubmitting: list[str] = field(default_factory=list)  # rows of IDs about to be replaced, ignored
    held: list[str] = field(default_factory=list)  # sheet behind the panel progress recorded in the app
    warnings: list[str] = field(default_factory=list)
    as_of: str = ""


def strip_prefix(name: str | None) -> str:
    return PREFIX.sub("", name or "").strip(" -·")


def latest_import(db: Session, account_id: int) -> ExcelImport | None:
    return db.scalar(
        select(ExcelImport)
        .where(ExcelImport.account_id == account_id)
        .order_by(ExcelImport.imported_at.desc(), ExcelImport.id.desc())
        .limit(1)
    )


def previous_import(db: Session, imp: ExcelImport) -> ExcelImport | None:
    return db.scalar(
        select(ExcelImport)
        .where(ExcelImport.account_id == imp.account_id, ExcelImport.id < imp.id)
        .order_by(ExcelImport.imported_at.desc(), ExcelImport.id.desc())
        .limit(1)
    )


def rows_of(db: Session, imp: ExcelImport) -> list[ExcelRow]:
    return list(
        db.scalars(select(ExcelRow).where(ExcelRow.import_id == imp.id).order_by(ExcelRow.row_number))
    )


# --- Tier 3: fuzzy suggestions ---------------------------------------------------------------------


def _score(row: ExcelRow, d: Demand) -> tuple[int, list[str]]:
    reasons: list[str] = []
    name = fuzz.token_set_ratio(strip_prefix(row.demand_request_name).casefold(), d.name.casefold())
    total = 0.35 * name
    reasons.append(f"name {round(name)}% alike")
    if row.originator and d.owner:
        who = fuzz.token_sort_ratio(row.originator.casefold(), d.owner.name.casefold())
        total += 0.20 * who
        if who >= 80:
            reasons.append("originator is the owner")
    for label, a, b, weight in (
        ("practice", row.practice, d.practice, 15),
        ("grade", row.grade, d.grade, 10),
        ("region", row.region, d.region, 5),
    ):
        if a and b and a.casefold() == b.casefold():
            total += weight
            reasons.append(f"same {label}")
    if row.start_date and d.start_date:
        gap = abs((row.start_date - d.start_date).days)
        if gap <= 7:
            total += 15 * (1 - gap / 14)
            reasons.append("same start date" if gap == 0 else f"start {gap} days apart")
    return round(total), reasons


def _suggest(rows: list[ExcelRow], candidates: list[Demand]) -> None:
    taken: set[int] = set()  # bulk slots: a later identical row gets the next free demand first
    for row in rows:
        scored = []
        for d in candidates:
            s, why = _score(row, d)
            if s >= SUGGEST_MIN:
                scored.append((s, d, why))
        scored.sort(key=lambda t: (-t[0], t[1].id in taken, t[1].app_ref))
        row.suggestions = [
            {"demand_id": d.id, "app_ref": d.app_ref, "name": d.name, "score": s, "reasons": why}
            for s, d, why in scored[:SUGGEST_MAX]
        ]
        if scored:
            taken.add(scored[0][1].id)
        row.outcome = (RowOutcome.SUGGESTED if scored else RowOutcome.UNMATCHED).value


# --- The run ---------------------------------------------------------------------------------------


def _resubmitting(db: Session, d: Demand) -> bool:
    """Sent back for correction or resubmission after its current ID was linked (not a demand whose
    first ID was just recorded, which is still Submitted until this run moves it)."""
    if d.status_enum not in RESUBMITTING or not d.submissions:
        return False
    if d.status_enum is DemandStatus.RETURNED:
        return True
    since = db.scalar(
        select(StageEvent.at)
        .where(StageEvent.demand_id == d.id, StageEvent.to_stage == d.status)
        .order_by(StageEvent.at.desc())
        .limit(1)
    )
    return since is not None and since > d.submissions[0].submitted_at


def reconcile(db: Session, imp: ExcelImport, actor_id: int, now: datetime | None = None) -> Summary:
    latest = latest_import(db, imp.account_id)
    if latest is None or latest.id != imp.id:
        raise ReconcileError("Only the latest DP sheet can be reconciled; older imports are history.")
    now = now or datetime.now(UTC)
    account = db.get_one(Account, imp.account_id)
    cfg: AccountConfig = account.settings
    tz = ZoneInfo(cfg.timezone)
    as_of: date = imp.sheet_date or now.astimezone(tz).date()
    summary = Summary(as_of=as_of.isoformat(), warnings=list(imp.summary.get("parse_warnings", [])))
    summary_parse_warnings = list(summary.warnings)

    demands = list(
        db.scalars(
            select(Demand)
            .where(Demand.account_id == account.id)
            .options(selectinload(Demand.submissions), selectinload(Demand.owner))
            # Links added by a person just before this run must be seen, not the session's stale copies.
            .execution_options(populate_existing=True)
        )
    )
    by_ref = {d.app_ref: d for d in demands}
    subs: dict[str, GtdSubmission] = {s.gtd_req_id: s for d in demands for s in d.submissions}
    sub_demand = {s.id: d for d in demands for s in d.submissions}

    rows = rows_of(db, imp)
    summary.rows = len(rows)
    seen: set[str] = set()
    in_sheet: dict[int, ExcelRow] = {}  # demand id → its row
    unmatched: list[ExcelRow] = []

    # 1. Match.
    for row in rows:
        kept = row.outcome == RowOutcome.CONFIRMED.value  # a person's decision survives re-runs
        if not kept:
            row.suggestions, row.note = [], None
        if not row.gtd_req_id:
            row.outcome, row.submission_id, row.match_tier = RowOutcome.INVALID.value, None, None
            continue
        if row.gtd_req_id in seen:
            row.outcome, row.submission_id, row.match_tier = RowOutcome.DUPLICATE.value, None, None
            row.note = "This requisition ID appears earlier in the sheet; only the first row counts."
            continue
        seen.add(row.gtd_req_id)

        sub = subs.get(row.gtd_req_id)
        if sub is not None and sub_demand[sub.id].submissions[0].id != sub.id:
            # An ID the demand has since replaced (resubmitted): it must not drive the demand any more.
            current = sub_demand[sub.id].submissions[0]
            row.outcome, row.submission_id, row.match_tier = RowOutcome.SUPERSEDED.value, None, None
            row.note = (
                f"{row.gtd_req_id} was replaced by {current.gtd_req_id} on {sub_demand[sub.id].app_ref}; "
                "this row is ignored."
            )
            summary.superseded.append(f"{sub_demand[sub.id].app_ref} ({row.gtd_req_id})")
            continue
        if sub is not None and _resubmitting(db, sub_demand[sub.id]):
            # Sent back for correction or resubmission: this ID is on its way out, so it doesn't move the
            # demand or reopen the escalation that was just settled.
            d = sub_demand[sub.id]
            row.outcome, row.submission_id, row.match_tier = RowOutcome.SUPERSEDED.value, None, None
            row.note = f"{d.app_ref} is being resubmitted; this row waits for its new requisition ID."
            summary.resubmitting.append(f"{d.app_ref} ({row.gtd_req_id})")
            continue
        if sub is not None:
            if not (kept or (row.outcome == RowOutcome.PREFIX.value and row.submission_id == sub.id)):
                row.outcome, row.match_tier = RowOutcome.MATCHED.value, 1
            row.submission_id = sub.id
            in_sheet[sub_demand[sub.id].id] = row
            continue

        m = PREFIX.search(row.demand_request_name or "")
        target = by_ref.get(m.group(1)) if m else None
        if target is not None:
            if target.status_enum in LINKABLE and not target.submissions:
                sub = GtdSubmission(
                    demand_id=target.id, gtd_req_id=row.gtd_req_id, submitted_by=actor_id, submitted_at=now
                )
                db.add(sub)
                db.flush()
                target.submissions.insert(0, sub)
                subs[sub.gtd_req_id], sub_demand[sub.id] = sub, target
                row.submission_id, row.match_tier, row.outcome = sub.id, 2, RowOutcome.PREFIX.value
                in_sheet[target.id] = row
                summary.prefix.append(target.app_ref)
                continue
            row.outcome, row.submission_id, row.match_tier = RowOutcome.CONFLICT.value, None, None
            row.note = (
                f"The name says {target.app_ref}, which is already linked to {target.gtd_req_id}."
                if target.gtd_req_id
                else f"The name says {target.app_ref}, which is {target.status_enum.label.lower()}."
            )
            continue
        row.submission_id, row.match_tier = None, None
        unmatched.append(row)

    candidates = [
        d
        for d in demands
        if d.id not in in_sheet and (d.status_enum in LINKABLE or d.status_enum in EXPECTED)
    ]
    _suggest(unmatched, candidates)

    # 2. Matched rows set the stage.
    for demand_id, row in in_sheet.items():
        d = next(x for x in demands if x.id == demand_id)
        stage = cfg.stage_for(row.status_group, row.status)
        if stage is None:
            label = " / ".join(x for x in (row.status_group, row.status) if x) or "(blank)"
            if label not in summary.unmapped_statuses:
                summary.unmapped_statuses.append(label)
            stage = (
                DemandStatus.LINKED if d.status_enum in (*LINKABLE, *EXPECTED, DemandStatus.DROPPED) else None
            )
        if stage is not None and pipeline_service.sheet_yields(db, d, stage):
            summary.held.append(d.app_ref)  # the sheet hasn't caught up with the panel yet
            stage = None
        if stage is not None:
            _move(db, d, stage, actor_id, imp, summary)
        if stage is DemandStatus.INCORRECT:
            summary.incorrect.append(d.app_ref)
            detail = f"DP sheet of {as_of:%d %b} marks {row.gtd_req_id} as '{row.status}'"
            _escalate(db, account, d, EscalationType.INCORRECT, detail, now, summary)
        if row.candidate_name and d.status_enum in interview_service.INTERVIEW_STAGES:
            # The sheet says who is on this requisition: that's how candidates get mapped (§7).
            interview_service.candidates_from_sheet(
                db, account.id, d, row.candidate_name, margin_service.channel_for_source(cfg, row.source)
            )
        if row.candidate_name and d.status_enum in SHEET_CANDIDATE_STAGES:
            interview_service.stage_from_sheet(db, account.id, d, row.candidate_name, d.status_enum.label)
        if stage is DemandStatus.OFFER_IN_PROCESS and row.candidate_name:
            # The offer needs a margin approval (§8); once per candidate.
            channel = margin_service.channel_for_source(cfg, row.source)
            if margin_service.ensure_offer(db, account, d, row.candidate_name, channel) is not None:
                summary.offers.append(d.app_ref)

    # Panel progress recorded in the app moves demands the sheet hasn't caught up with.
    for demand_id in in_sheet:
        d = next(x for x in demands if x.id == demand_id)
        before = d.status_enum
        if pipeline_service.apply(db, account, d, actor_id) is not None:
            summary.stage_changes.append({"ref": d.app_ref, "from": before.label, "to": d.status_enum.label})

    # 3. Sent to GTD, not in the sheet: awaiting within the grace period, then missing.
    for d in demands:
        if d.id in in_sheet or d.status_enum not in EXPECTED or not d.submissions:
            continue
        linked_on = d.submissions[0].submitted_at.astimezone(tz).date()
        deadline = add_working_days(linked_on, account.grace_days)
        if as_of <= deadline:
            summary.awaiting.append(d.app_ref)
            continue
        summary.missing.append(d.app_ref)
        _move(db, d, DemandStatus.MISSING, actor_id, imp, summary)
        _escalate(db, account, d, EscalationType.MISSING,
                  f"Linked to {d.gtd_req_id} on {linked_on:%d %b}; not in the DP sheet of {as_of:%d %b} "
                  f"({account.grace_days} working days' grace)", now, summary)  # fmt: skip

    # 4. In the previous sheet, gone now, still open: dropped.
    prev = previous_import(db, imp)
    if prev is not None:
        prev_date = prev.sheet_date.strftime("%d %b") if prev.sheet_date else "the previous sheet"
        # Only rows of a demand's current requisition count: after a resubmit the old ID is expected to go.
        prev_demands = {
            sub_demand[r.submission_id].id
            for r in rows_of(db, prev)
            if r.submission_id is not None
            and r.submission_id in sub_demand
            and sub_demand[r.submission_id].submissions[0].id == r.submission_id
        }
        for d in demands:
            if (
                d.id in prev_demands
                and d.id not in in_sheet
                and d.status_enum not in FINISHED
                and not _resubmitting(db, d)
            ):
                summary.dropped.append(d.app_ref)
                _move(db, d, DemandStatus.DROPPED, actor_id, imp, summary)
                _escalate(db, account, d, EscalationType.DROPPED,
                          f"{d.gtd_req_id} was in the DP sheet of {prev_date}, gone from {as_of:%d %b}",
                          now, summary)  # fmt: skip

    counts = [RowOutcome(r.outcome) for r in rows]
    summary.in_sheet = len(in_sheet)
    summary.matched = counts.count(RowOutcome.MATCHED)
    summary.confirmed = counts.count(RowOutcome.CONFIRMED)
    summary.needs_person = sum(o.needs_person for o in counts)
    summary.duplicates = counts.count(RowOutcome.DUPLICATE)
    summary.invalid = counts.count(RowOutcome.INVALID)
    summary.sent = len(in_sheet) + len(summary.awaiting) + len(summary.missing) + len(summary.dropped)
    if summary.unmapped_statuses:
        summary.warnings.append(
            "Statuses with no mapping (demands stay Linked): " + ", ".join(summary.unmapped_statuses)
            + ". Add them under Account settings → DP sheet status mapping."
        )  # fmt: skip
    imp.summary = asdict(summary) | {"parse_warnings": summary_parse_warnings}
    db.commit()
    try:  # heads-up to interviewers; a mail problem must not undo the import
        interview_service.alert_interviewers(db, account, list(in_sheet))
        db.commit()
    except Exception:
        db.rollback()
        log.exception("Interviewer alerts failed for import %s", imp.id)
    return summary


def _move(
    db: Session, d: Demand, to: DemandStatus, actor_id: int, imp: ExcelImport, summary: Summary
) -> None:
    before = d.status_enum
    if record_stage(db, d, to, actor_id, StageOrigin.IMPORT, imp.id):
        summary.stage_changes.append({"ref": d.app_ref, "from": before.label, "to": to.label})


def _escalate(
    db: Session,
    account: Account,
    d: Demand,
    type_: EscalationType,
    detail: str,
    now: datetime,
    summary: Summary,
) -> None:
    if open_escalation(db, account, d, type_, detail, now) is not None:
        summary.new_escalations.append(d.app_ref)


# --- A person resolves a row ------------------------------------------------------------------------


def _actionable_row(db: Session, account_id: int, row_id: int) -> tuple[ExcelImport, ExcelRow]:
    row = db.get(ExcelRow, row_id)
    imp = db.get(ExcelImport, row.import_id) if row else None
    if row is None or imp is None or imp.account_id != account_id:
        raise ReconcileError("Row not found.")
    latest = latest_import(db, account_id)
    if latest is None or latest.id != imp.id:
        raise ReconcileError("That row belongs to an older DP sheet; only the latest can be changed.")
    if not RowOutcome(row.outcome).needs_person:
        raise ReconcileError("That row is already matched.")
    if not row.gtd_req_id:
        raise ReconcileError("That row has no requisition ID to link.")
    taken = db.scalar(
        select(Demand.app_ref).join(GtdSubmission).where(GtdSubmission.gtd_req_id == row.gtd_req_id)
    )
    if taken:
        raise ReconcileError(f"{row.gtd_req_id} is already linked to {taken}.")
    return imp, row


def confirm_match(db: Session, account_id: int, actor_id: int, row_id: int, demand_id: int) -> Demand:
    """Link a sheet row to a demand a person picked (a suggestion or any open demand)."""
    imp, row = _actionable_row(db, account_id, row_id)
    d = db.scalar(
        select(Demand)
        .where(Demand.id == demand_id, Demand.account_id == account_id)
        .options(selectinload(Demand.submissions))
    )
    if d is None or d.status_enum in FINISHED or d.status_enum is DemandStatus.DRAFT:
        raise ReconcileError("Pick an open demand that has been submitted.")
    already = db.scalar(
        select(ExcelRow.id)
        .join(GtdSubmission, GtdSubmission.id == ExcelRow.submission_id)
        .where(ExcelRow.import_id == imp.id, GtdSubmission.demand_id == d.id)
    )
    if already:
        raise ReconcileError(f"{d.app_ref} already has a row in this sheet.")
    # A demand linked to another ID gets a corrected link that chains to the old one.
    previous = d.submissions[0].id if d.submissions else None
    sub = GtdSubmission(demand_id=d.id, gtd_req_id=row.gtd_req_id, submitted_by=actor_id,
                        previous_submission_id=previous)  # fmt: skip
    db.add(sub)
    db.flush()
    row.submission_id, row.match_tier, row.outcome = sub.id, 3, RowOutcome.CONFIRMED.value
    row.matched_by, row.suggestions = actor_id, []
    row.note = f"Matched to {d.app_ref} by hand" + (" (replaces an earlier ID)" if previous else "")
    db.flush()
    reconcile(db, imp, actor_id)
    return d


def owner_for_originator(db: Session, account_id: int, originator: str | None) -> User | None:
    """The sheet's originator, if their name matches an active demand owner or GTD team admin."""
    if not originator:
        return None
    people = list(db.scalars(select(User).where(member_of(account_id, Role.DEMAND_OWNER, Role.ADMIN))))
    best = max(
        people, key=lambda u: fuzz.token_sort_ratio(u.name.casefold(), originator.casefold()), default=None
    )
    if best and fuzz.token_sort_ratio(best.name.casefold(), originator.casefold()) >= 90:
        return best
    return None


def create_from_row(
    db: Session, account_id: int, actor_id: int, row_id: int, owner_id: int, bu_id: int | None
) -> Demand:
    """A row raised outside the app becomes a demand, already linked to its requisition ID."""
    imp, row = _actionable_row(db, account_id, row_id)
    owner = db.scalar(
        select(User)
        .where(User.id == owner_id, member_of(account_id, Role.DEMAND_OWNER, Role.ADMIN))
        .options(selectinload(User.business_units), selectinload(User.memberships))
    )
    if owner is None:
        raise ReconcileError("Pick an active demand owner or GTD team admin as the owner.")
    if owner.membership(account_id).role == Role.DEMAND_OWNER.value:  # type: ignore[union-attr]
        bus = [b for b in owner.business_units if b.account_id == account_id]
        if len(bus) != 1:
            raise ReconcileError(f"{owner.name} has no single business unit.")
        bu_id = bus[0].id
    bu = db.get(BusinessUnit, bu_id) if bu_id else None
    if bu is None or bu.account_id != account_id or not bu.active:
        raise ReconcileError("Pick the business unit.")
    if not row.practice or not row.grade:
        raise ReconcileError("The row has no practice or grade; raise the demand by hand instead.")

    d = Demand(
        account_id=account_id, bu_id=bu.id, owner_id=owner.id,
        name=strip_prefix(row.demand_request_name)[:200] or row.gtd_req_id,
        practice=row.practice, grade=row.grade, region=row.region, start_date=row.start_date,
        status=DemandStatus.SENT_TO_GTD.value, submitted_at=datetime.now(UTC),
        custom_fields={"created_from_dp_row": row.id},
    )  # fmt: skip
    db.add(d)
    db.flush()
    db.add(StageEvent(demand_id=d.id, from_stage=None, to_stage=d.status, origin=StageOrigin.IMPORT.value,
                      actor_id=actor_id, import_id=imp.id))  # fmt: skip
    sub = GtdSubmission(demand_id=d.id, gtd_req_id=row.gtd_req_id, submitted_by=actor_id)
    db.add(sub)
    db.flush()
    row.submission_id, row.match_tier, row.outcome = sub.id, 3, RowOutcome.CONFIRMED.value
    row.matched_by, row.suggestions, row.note = actor_id, [], f"Demand {d.app_ref} created from this row"
    db.flush()
    reconcile(db, imp, actor_id)
    return d


def summary_of(imp: ExcelImport) -> dict[str, Any]:
    return dict(imp.summary or {})
