"""Account settings: client-specific rules change here, not in code."""

from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.core.account_config import AccountConfig
from app.core.enums import DemandStatus
from app.models import Account, BusinessUnit
from tests.conftest import Client, bu_id

THRESHOLDS = {
    "grace_days": "5",
    "l1_sla_days": "2",
    "l2_sla_days": "4",
    "panel_timer_hours": "24",
    "aging_days": "10",
    "rejection_limit": "4",
    "margin_threshold": "32.5",
    "mail_time": "08:30",
    "timezone": "America/New_York",
}


@pytest.fixture
def admin(client: Client) -> Client:
    return client.as_user("kavya")


def account(db: Session) -> Account:
    return db.query(Account).one()


def test_update_thresholds(admin: Client, db: Session) -> None:
    r = admin.post("/settings/thresholds", data=THRESHOLDS, follow_redirects=False)
    assert "msg=Saved" in r.headers["location"]
    a = account(db)
    assert (a.grace_days, a.l2_sla_days, a.rejection_limit) == (5, 4, 4)
    assert a.margin_threshold == Decimal("32.50")
    assert a.mail_time.strftime("%H:%M") == "08:30"
    assert a.settings.timezone == "America/New_York"


@pytest.mark.parametrize(
    ("field", "value"), [("grace_days", "-1"), ("margin_threshold", "140"), ("timezone", "Mars/Olympus")]
)
def test_invalid_thresholds_rejected(admin: Client, db: Session, field: str, value: str) -> None:
    r = admin.post("/settings/thresholds", data={**THRESHOLDS, field: value}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    assert account(db).grace_days == 3


def test_add_rename_retire_business_unit(admin: Client, db: Session) -> None:
    admin.post("/settings/business-units", data={"name": " wealth "})
    wealth = bu_id("WEALTH")
    r = admin.post("/settings/business-units", data={"name": "CARDS"}, follow_redirects=False)
    assert "already+exists" in r.headers["location"] or "already%20exists" in r.headers["location"]
    admin.post(f"/settings/business-units/{wealth}", data={"name": "Wealth Mgmt", "active": "0"})
    bu = db.get_one(BusinessUnit, wealth)
    assert (bu.name, bu.active) == ("WEALTH MGMT", False)


def test_retired_bu_not_offered_to_new_users(admin: Client) -> None:
    admin.post(f"/settings/business-units/{bu_id('DATA')}", data={"name": "DATA", "active": "0"})
    html = admin.get("/users?new=1").text
    assert f'value="{bu_id("DATA")}"' not in html


def test_status_mapping_round_trip(admin: Client, db: Session) -> None:
    data = {
        "status_group": ["Work in Progress", "Staffed", ""],
        "status": ["Coverage Required", "Allocation Completed", ""],
        "stage": ["coverage_required", "staffed", "coverage_required"],
    }
    r = admin.post("/settings/status-mapping", data=data, follow_redirects=False)
    assert "msg=Saved" in r.headers["location"]
    cfg = account(db).settings
    assert len(cfg.status_mapping) == 2  # blank row dropped
    assert cfg.stage_for("Staffed", "allocation completed") is DemandStatus.STAFFED


@pytest.mark.parametrize("stage", ["draft", "sent_to_gtd", "nonsense"])
def test_mapping_rejects_non_sheet_stages(admin: Client, db: Session, stage: str) -> None:
    r = admin.post(
        "/settings/status-mapping",
        data={"status_group": ["X"], "status": ["Y"], "stage": [stage]},
        follow_redirects=False,
    )
    assert "err=" in r.headers["location"]
    assert len(account(db).settings.status_mapping) == 7


def test_mapping_rejects_duplicates(admin: Client) -> None:
    data = {"status_group": ["A", "a"], "status": ["Open", "open "], "stage": ["coverage_required"] * 2}
    r = admin.post("/settings/status-mapping", data=data, follow_redirects=False)
    assert "mapped+twice" in r.headers["location"] or "mapped%20twice" in r.headers["location"]


def test_lists_and_channels(admin: Client, db: Session) -> None:
    admin.post("/settings/lists", data={"practices": "CCA-FS\r\nNEW-FS\r\n\r\nCCA-FS"})
    assert account(db).settings.practices == ["CCA-FS", "NEW-FS"]
    r = admin.post("/settings/lists", data={"grades": "  \n"}, follow_redirects=False)
    assert "err=" in r.headers["location"]
    admin.post(
        "/settings/supply-channels",
        data={
            "label": ["Sogeti", "", "Partner X"],
            "sheet_marker": ["Sogeti Supply", "", "PX"],
            "needs_sourcing_req": ["", "", "on"],
        },
    )
    db.expire_all()
    chans = account(db).settings.supply_channels
    assert [(c.label, c.needs_sourcing_req) for c in chans] == [("Sogeti", False), ("Partner X", True)]


def test_seeded_mapping_matches_flow_artifact() -> None:
    from seed.data import CONFIG

    cfg = AccountConfig.model_validate(CONFIG)
    assert cfg.stage_for("Offer in Market/Process", "Offer in Market") is DemandStatus.OFFER_IN_MARKET
    assert cfg.stage_for(None, "In Correct Demnad") is DemandStatus.INCORRECT
    assert cfg.stage_for("Work in Progress", "Something new") is None
    assert cfg.grade_rank("D1") > cfg.grade_rank("C2")
