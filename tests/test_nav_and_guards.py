"""Each persona's menu, landing page and route guards."""

import re

import pytest

from app.core.config import Settings, get_settings
from tests.conftest import Client


def menu(client: Client) -> list[str]:
    html = client.get("/", follow_redirects=True).text
    nav = html[html.index('aria-label="Main"') : html.index("</nav>")]
    return [
        re.sub(r"\s+P\d$", "", re.sub(r"<[^>]+>", " ", m).strip()).strip()
        for m in re.findall(r'class="nav-link[^"]*"[^>]*>(.*?)</a>', nav, re.S)
    ]


def test_demand_owner_menu(client: Client) -> None:
    assert menu(client.as_user("priya")) == ["My demands", "Raise demand"]


def test_admin_menu(client: Client) -> None:
    items = menu(client.as_user("kavya"))
    assert items[0] == "All demands"
    for expected in (
        "GTD queue",
        "DP sheet import",
        "Reconciliation",
        "Escalations",
        "Rate card",
        "User access",
        "Account settings",
    ):
        assert expected in items
    assert "My interviews" not in items


def test_leadership_menu(client: Client) -> None:
    items = menu(client.as_user("sanjay"))
    assert items == ["Account overview", "All demands", "Escalations", "Offer approvals"]


def test_interviewer_menu_and_home(client: Client) -> None:
    client.as_user("vikram")
    assert menu(client) == ["My interviews"]
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/soon/interviews"


@pytest.mark.parametrize(
    ("who", "path"),
    [
        ("priya", "/users"),
        ("priya", "/settings"),
        ("sanjay", "/users"),
        ("sanjay", "/settings"),
        ("vikram", "/demands"),
        ("rahul", "/api/users"),
    ],
)
def test_role_guard_blocks(client: Client, who: str, path: str) -> None:
    assert client.as_user(who).get(path).status_code == 403


def test_role_guard_blocks_writes(client: Client) -> None:
    r = client.as_user("priya").post("/settings/business-units", data={"name": "HACK"})
    assert r.status_code == 403


def test_soon_page_only_for_own_menu(client: Client) -> None:
    client.as_user("priya")
    assert client.get("/soon/raise").status_code == 200
    assert client.get("/soon/gtd_queue").status_code == 404


def test_view_as_switches_persona(client: Client) -> None:
    from tests.conftest import user_id

    r = client.get(f"/view-as/{user_id('kavya')}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/demands"
    assert "All demands" in client.get("/demands").text


def test_switcher_cannot_be_on_in_production() -> None:
    with pytest.raises(ValueError, match="VIEW_SWITCHER_ENABLED"):
        Settings(env="production", view_switcher_enabled=True)


def test_without_switcher_there_is_no_way_in(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    monkeypatch.setenv("VIEW_SWITCHER_ENABLED", "false")
    get_settings.cache_clear()
    try:
        with Client(create_app(), follow_redirects=False) as c:
            c.as_user("kavya")
            assert c.get("/view-as/1").status_code == 404
            assert c.get("/api/demands").status_code == 401
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
