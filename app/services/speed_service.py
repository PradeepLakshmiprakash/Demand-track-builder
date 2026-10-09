"""The speed report: how long demands take, and where the time goes.

Everything here is read from what the app already records: the dated step history of each demand
(stage events), the candidates who did not join, and the first billable day. Durations are working days.
A step's time counts once the demand has left it; time to fill counts once the candidate has joined.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.enums import DemandStatus
from app.models import Account, BusinessUnit, CandidateExit, Demand, StageEvent, User
from app.services import onboarding_service, workflow_service
from app.services.loss_service import working_days

S = DemandStatus
# The steps that take time, in workflow order. Submitted and notified are one step to a reader.
STEPS: tuple[DemandStatus, ...] = (
    S.SUBMITTED,
    S.SENT_TO_GTD,
    S.COVERAGE_REQUIRED,
    S.INTERVIEWING,
    S.PANEL_SELECTED,
    S.PROFILES_WITH_CLIENT,
    S.OFFER_IN_PROCESS,
    S.OFFER_IN_MARKET,
)
SAME_STEP = {S.NOTIFIED.value: S.SUBMITTED.value}
PERIODS = (30, 90, 180, 365)
GROUPS = {"bu": "Business unit", "practice": "Practice", "owner": "Demand owner"}


@dataclass
class StepTime:
    key: str
    no: int  # the step's number in the workflow
    label: str
    days: float | None  # median working days; None when no demand left the step in the period
    count: int
    target: int | None

    @property
    def over(self) -> bool:
        return self.days is not None and self.target is not None and self.days > self.target


@dataclass
class Stay:
    """One demand's time at one step."""

    demand: Demand
    days: int
    entered: date
    left: date


@dataclass
class GroupRow:
    name: str
    filled: int = 0
    fill_days: list[int] = field(default_factory=list)
    late: int = 0
    exits: int = 0

    @property
    def time_to_fill(self) -> float | None:
        return median(self.fill_days) if self.fill_days else None


@dataclass
class Report:
    since: date
    today: date
    time_to_fill: float | None
    fill_target: int | None
    filled: int
    filled_late: int
    exits: int
    offers: int
    billing_gap: float | None
    billing_target: int | None
    steps: list[StepTime]
    groups: list[GroupRow]
    reasons: list[tuple[str, int]]
    stays: dict[str, list[Stay]]
    fills: list[tuple[Demand, int, bool]]  # demand, working days to fill, joined after the start date

    @property
    def longest(self) -> float:
        return max([s.days for s in self.steps if s.days] + [s.target or 0 for s in self.steps] + [1])

    @property
    def exit_share(self) -> int | None:
        return round(self.exits / self.offers * 100) if self.offers else None


def _med(values: list[int]) -> float | None:
    return float(median(values)) if values else None


def report(
    db: Session,
    account: Account,
    today: date,
    days: int = 90,
    bu: str = "",
    practice: str = "",
    owner: str = "",
    by: str = "bu",
) -> Report:
    since = today - timedelta(days=days)
    cfg = account.settings.onboarding
    stmt = select(Demand).where(Demand.account_id == account.id, Demand.status != S.DRAFT.value)
    if practice:
        stmt = stmt.where(Demand.practice == practice)
    if bu:
        stmt = stmt.where(Demand.bu_id.in_(select(BusinessUnit.id).where(BusinessUnit.name == bu)))
    if owner.isdigit():
        stmt = stmt.where(Demand.owner_id == int(owner))
    demands = {d.id: d for d in db.scalars(stmt)}
    events: dict[int, list[StageEvent]] = {}
    if demands:
        for e in db.scalars(
            select(StageEvent).where(StageEvent.demand_id.in_(demands)).order_by(StageEvent.at, StageEvent.id)
        ):
            events.setdefault(e.demand_id, []).append(e)

    stays: dict[str, list[Stay]] = {s.value: [] for s in STEPS}
    fills: list[tuple[Demand, int, bool]] = []
    offers = 0
    joined = onboarding_service.joined_on(
        db, account.id, [d for d in demands.values() if d.status_enum is S.STAFFED]
    )
    for demand_id, evs in events.items():
        d = demands[demand_id]
        for now, nxt in zip(evs, evs[1:], strict=False):
            step = SAME_STEP.get(now.to_stage, now.to_stage)
            left = nxt.at.date()
            if SAME_STEP.get(nxt.to_stage, nxt.to_stage) == step or left < since:
                continue
            if step in stays:
                stays[step].append(Stay(d, working_days(now.at.date(), left), now.at.date(), left))
        offers += sum(e.to_stage == S.OFFER_IN_PROCESS.value and e.at.date() >= since for e in evs)
        done = [e for e in evs if e.to_stage == S.STAFFED.value]
        if d.status_enum is S.STAFFED and done and done[-1].at.date() >= since:
            first = next((e for e in evs if e.to_stage != S.DRAFT.value), evs[0])
            began = min(first.at.date(), d.submitted_at.date()) if d.submitted_at else first.at.date()
            day = joined.get(d.id) or done[-1].at.date()
            start = d.loss_from
            fills.append((d, working_days(min(began, day), day), bool(start and day > start)))

    for key in stays:
        stays[key].sort(key=lambda s: -s.days)
    steps = [
        StepTime(
            key=s.value,
            no=workflow_service.number(s),
            label=s.label,
            days=_med([x.days for x in stays[s.value]]),
            count=len(stays[s.value]),
            target=cfg.targets.get(s.value),
        )
        for s in STEPS
    ]

    exits = [
        x
        for x in db.scalars(
            select(CandidateExit).where(
                CandidateExit.account_id == account.id, CandidateExit.on_date >= since
            )
        )
        if x.demand_id in demands
    ]
    reasons: dict[str, int] = {}
    for x in exits:
        reasons[x.reason] = reasons.get(x.reason, 0) + 1

    owners: dict[int, str] = {}
    if by == "owner":
        owners = {uid: name for uid, name in db.execute(select(User.id, User.name))}

    def group(d: Demand) -> str:
        if by == "practice":
            return d.practice or "—"
        if by == "owner":
            return owners.get(d.owner_id, "—")
        return d.business_unit.name

    rows: dict[str, GroupRow] = {}
    for d, n, late in fills:
        r = rows.setdefault(group(d), GroupRow(group(d)))
        r.filled, r.late = r.filled + 1, r.late + late
        r.fill_days.append(n)
    for x in exits:
        d = demands[x.demand_id]
        rows.setdefault(group(d), GroupRow(group(d))).exits += 1

    gaps = [
        b.waiting_days
        for i, b in onboarding_service.billing(db, account, list(demands.values()), today).items()
        if b.started and b.started >= since
    ]
    fills.sort(key=lambda f: -f[1])
    return Report(
        since=since,
        today=today,
        time_to_fill=_med([n for _, n, _ in fills]),
        fill_target=cfg.targets.get("time_to_fill"),
        filled=len(fills),
        filled_late=sum(late for _, _, late in fills),
        exits=len(exits),
        offers=max(offers, len(exits)),
        billing_gap=_med(gaps),
        billing_target=cfg.targets.get("joined_to_billing"),
        steps=steps,
        groups=sorted(rows.values(), key=lambda r: (-(r.time_to_fill or 0), r.name)),
        reasons=sorted(reasons.items(), key=lambda p: (-p[1], p[0])),
        stays=stays,
        fills=fills,
    )
