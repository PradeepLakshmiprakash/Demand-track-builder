"""Sidebar menu per role. One registry, so a persona's menu and its route guards can't drift apart."""

from dataclasses import dataclass

from app.core.enums import Role

DO, AD, AT, LD, IV = Role.DEMAND_OWNER, Role.ADMIN, Role.ADMIN_TEAM, Role.LEADERSHIP, Role.INTERVIEWER
ADM = Role.ADMINISTRATOR  # app controls only: no demand screens


@dataclass(frozen=True)
class NavItem:
    key: str
    path: str
    labels: dict[Role, str]
    phase: int = 1  # plan.md phase that delivers the screen
    ready: bool = False

    def label_for(self, role: Role) -> str:
        return self.labels[role]


def _same(label: str, *roles: Role) -> dict[Role, str]:
    return dict.fromkeys(roles, label)


NAV: tuple[NavItem, ...] = (
    NavItem("overview", "/overview", _same("Account overview", LD, AD), phase=5, ready=True),
    NavItem(
        "demands",
        "/demands",
        {DO: "My demands", AD: "All demands", AT: "All demands", LD: "All demands"},
        ready=True,
    ),
    NavItem("raise", "/demands/new", _same("Raise demand", DO, AD), phase=2, ready=True),
    NavItem("gtd_queue", "/gtd-queue", _same("GTD queue", AD, AT), phase=2, ready=True),
    NavItem("import", "/imports", _same("DP sheet import", AD, AT), phase=3, ready=True),
    NavItem("reconciliation", "/reconciliation", _same("Reconciliation", AD, AT), phase=3, ready=True),
    NavItem("escalations", "/escalations", _same("Escalations", AD, AT, LD), phase=4, ready=True),
    NavItem("approvals", "/approvals", _same("Offer approvals", AD, LD), phase=5, ready=True),
    NavItem("requests", "/requests", {AD: "Requests to administrator", ADM: "Requests"}, phase=8, ready=True),
    NavItem("candidates", "/candidates", _same("Candidates", AD, AT), phase=6, ready=True),
    NavItem("interviews", "/interviews", _same("My interviews", IV), phase=6, ready=True),
    NavItem("interviewer_profiles", "/interviewers", _same("Interviewer profiles", AD), phase=6, ready=True),
    NavItem("users", "/users", _same("User access", ADM), ready=True),
    NavItem("settings", "/settings", _same("Account settings", ADM), ready=True),
    NavItem("rate_card", "/rate-card", _same("Rate card", ADM), phase=5, ready=True),
)

BY_KEY = {n.key: n for n in NAV}


def menu_for(role: Role) -> list[NavItem]:
    return [n for n in NAV if role in n.labels]


# Where each role lands. The GTD team admin's day starts on the demands, not the overview.
HOME = {AD: "demands", ADM: "requests"}


def home_for(role: Role) -> str:
    """The role's home screen, else the first ready one; an unbuilt one shows its 'coming in phase N' view."""
    items = menu_for(role)
    preferred = BY_KEY.get(HOME.get(role, ""))
    if preferred is not None and preferred.ready and role in preferred.labels:
        return preferred.path
    for n in items:
        if n.ready:
            return n.path
    return f"/soon/{items[0].key}"
