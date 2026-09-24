"""Sidebar menu per role. One registry, so a persona's menu and its route guards can't drift apart."""

from dataclasses import dataclass

from app.core.enums import Role

DO, AD, AT, LD, IV = Role.DEMAND_OWNER, Role.ADMIN, Role.ADMIN_TEAM, Role.LEADERSHIP, Role.INTERVIEWER


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
    NavItem("overview", "/overview", _same("Account overview", LD), phase=5),
    NavItem(
        "demands",
        "/demands",
        {DO: "My demands", AD: "All demands", AT: "All demands", LD: "All demands"},
        ready=True,
    ),
    NavItem("raise", "/demands/new", _same("Raise demand", DO, AD), phase=2, ready=True),
    NavItem("gtd_queue", "/gtd-queue", _same("GTD queue", AD, AT), phase=2, ready=True),
    NavItem("import", "/imports", _same("DP sheet import", AD, AT), phase=3),
    NavItem("reconciliation", "/reconciliation", _same("Reconciliation", AD, AT), phase=3),
    NavItem("escalations", "/escalations", _same("Escalations", AD, AT, LD), phase=4),
    NavItem("approvals", "/approvals", _same("Offer approvals", AD, LD), phase=5),
    NavItem("rate_card", "/rate-card", _same("Rate card", AD), phase=5),
    NavItem("interviews", "/interviews", _same("My interviews", IV), phase=6),
    NavItem("interviewer_profiles", "/interviewers", _same("Interviewer profiles", AD), phase=6),
    NavItem("users", "/users", _same("User access", AD), ready=True),
    NavItem("settings", "/settings", _same("Account settings", AD), ready=True),
)

BY_KEY = {n.key: n for n in NAV}


def menu_for(role: Role) -> list[NavItem]:
    return [n for n in NAV if role in n.labels]


def home_for(role: Role) -> str:
    """First ready screen in the role's menu; an unbuilt landing page shows its 'coming in phase N' view."""
    items = menu_for(role)
    for n in items:
        if n.ready:
            return n.path
    return f"/soon/{items[0].key}"
