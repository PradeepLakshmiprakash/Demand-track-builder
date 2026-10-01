"""Flow 3: DP sheet import and reconciliation. Includes the Phase 3 exit check."""

from datetime import date, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.account_config import AccountConfig
from app.models import Account, Demand, Escalation, ExcelImport, ExcelRow, GtdSubmission, StageEvent
from app.services import reconcile_service
from app.services.excel_parser import SheetError, parse
from app.services.import_service import date_from_filename
from seed import sample_sheet
from seed.data import CONFIG
from tests.conftest import Client, bu_id, user_id

TODAY = date.today()
CFG = AccountConfig.model_validate(CONFIG)


def by_ref(db: Session, ref: str) -> Demand:
    db.expire_all()
    return db.scalars(select(Demand).where(Demand.app_ref == ref)).one()


def upload(
    client: Client, data: bytes, sheet_date: date = TODAY, name: str = "dp.xlsx", **extra: str
) -> object:
    return client.post(
        "/imports",
        data={"sheet_date": sheet_date.isoformat(), **extra},
        files={"file": (name, data, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )


@pytest.fixture
def team(client: Client) -> Client:
    return client.as_user("farah")


def latest(db: Session) -> ExcelImport:
    db.expire_all()
    imp = reconcile_service.latest_import(db, 1)
    assert imp is not None
    return imp


def row(db: Session, req: str) -> ExcelRow:
    return db.scalars(select(ExcelRow).where(ExcelRow.gtd_req_id == req).order_by(ExcelRow.id.desc())).first()  # type: ignore[return-value]


# --- Parser ---------------------------------------------------------------------------------------


def test_parser_finds_header_below_title_and_cleans_blanks() -> None:
    sheet = parse(sample_sheet.build(), CFG)
    assert len(sheet.rows) == len(sample_sheet.ROWS) and sheet.warnings == []
    first = sheet.rows[0]
    assert first.row_number == 3  # title row, header row, then data
    assert first.values["req_id"] == "2ZT7KP"
    assert first.values["source"] is None  # "0" means empty
    assert first.values["gettalent_req_id"] is None  # "-" means empty
    assert first.values["start_date"] == date(2026, 10, 27)
    incorrect = next(r for r in sheet.rows if r.values["req_id"] == "M2PX6D")
    assert incorrect.values["status_group"] is None
    assert first.raw["Code Requisition"] == "2ZT7KP"


def test_parser_reports_missing_required_columns() -> None:
    headers = [h if h != "Code Requisition" else "Req" for h in sample_sheet.HEADERS]
    with pytest.raises(SheetError, match="No header row with 'Code Requisition'"):
        parse(sample_sheet.build(headers=headers), CFG)
    headers = [h if h != "Status" else "State" for h in sample_sheet.HEADERS]
    with pytest.raises(SheetError, match="missing required columns: Status"):
        parse(sample_sheet.build(headers=headers), CFG)


def test_parser_warns_about_missing_optional_columns() -> None:
    headers = [h if h != "DOJ" else "Joining" for h in sample_sheet.HEADERS]
    sheet = parse(sample_sheet.build(headers=headers), CFG)
    assert "Date of joining" in sheet.warnings[0]


def test_parser_rejects_non_excel() -> None:
    with pytest.raises(SheetError, match="isn't an Excel"):
        parse(b"not a workbook", CFG)


def test_sheet_date_from_file_name() -> None:
    assert date_from_filename("Discover_NA_PSCM_Coverage_Summary_9-Sep-2026.xlsx") == date(2026, 9, 9)
    assert date_from_filename("dp_2026-10-02.xlsx") == date(2026, 10, 2)
    assert date_from_filename("dp.xlsx") is None


# --- Phase 3 exit ---------------------------------------------------------------------------------


def test_phase3_exit_sample_sheet_links_everything_and_flags_missing(team: Client, db: Session) -> None:
    r = upload(team, sample_sheet.build())
    assert r.headers["location"].startswith("/reconciliation?msg=Imported")  # type: ignore[attr-defined]
    s = latest(db).summary

    # Every seeded demand in the sheet is linked, and none is linked twice.
    assert s["in_sheet"] == 13 and s["matched"] == 12 and s["prefix"] == ["DM-000147"]
    linked = {row(db, x[1]).submission_id for x in sample_sheet.ROWS[:13]}
    assert None not in linked and len(linked) == 13

    # The deliberately missing demand is flagged and escalated.
    assert s["missing"] == ["DM-000139", "DM-000148"]
    assert by_ref(db, "DM-000148").status == "missing"
    esc = db.scalars(select(Escalation).where(Escalation.demand_id == by_ref(db, "DM-000148").id)).one()
    assert (esc.type, esc.level, esc.status) == ("missing", 1, "open") and "EKT8HQ" in (esc.detail or "")

    # Sheet status sets the stage; incorrect demand is flagged.
    assert by_ref(db, "DM-000147").status == "coverage_required"
    assert by_ref(db, "DM-000116").status == "staffed"  # "Allocation Pending " with a trailing space
    assert s["incorrect"] == ["DM-000133"]
    assert s["needs_person"] == 2 and s["sent"] == 15


def test_prefix_match_records_the_requisition_id(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build())
    d = by_ref(db, "DM-000147")
    assert d.gtd_req_id == "P7KD2M"
    assert row(db, "P7KD2M").match_tier == 2
    ev = db.scalars(
        select(StageEvent).where(StageEvent.demand_id == d.id).order_by(StageEvent.id.desc())
    ).first()
    assert ev is not None and ev.origin == "import" and ev.import_id == latest(db).id


def test_reconciliation_page(team: Client) -> None:
    upload(team, sample_sheet.build())
    page = team.get("/reconciliation").text
    assert "Missing from the sheet" in page and "DM-000148" in page
    assert "W3NX5A" in page and "N9T49U" in page and "98%" in page


# --- People resolve rows --------------------------------------------------------------------------


def test_confirm_fuzzy_suggestion(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build())
    r = row(db, "W3NX5A")
    assert r.outcome == "suggested" and r.suggestions[0]["app_ref"] == "DM-000151"
    res = team.post(
        f"/reconciliation/rows/{r.id}/confirm", data={"demand_id": str(r.suggestions[0]["demand_id"])}
    )
    assert "Row+linked+to+DM-000151" in res.headers["location"] or "DM-000151" in res.headers["location"]
    d = by_ref(db, "DM-000151")
    assert d.gtd_req_id == "W3NX5A" and d.status == "coverage_required"
    db.expire_all()
    assert row(db, "W3NX5A").outcome == "confirmed" and row(db, "W3NX5A").matched_by == user_id("farah")
    # A re-run keeps the person's decision.
    team.post(f"/imports/{latest(db).id}/rerun")
    db.expire_all()
    assert row(db, "W3NX5A").outcome == "confirmed" and latest(db).summary["needs_person"] == 1


def test_create_demand_from_row(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build())
    r = row(db, "N9T49U")
    assert r.outcome == "unmatched"
    assert reconcile_service.owner_for_originator(db, 1, r.originator).name == "Meera S."  # type: ignore[union-attr]
    team.post(f"/reconciliation/rows/{r.id}/create", data={"owner_id": str(user_id("meera")), "bu_id": ""})
    db.expire_all()
    d = db.scalars(select(Demand).join(GtdSubmission).where(GtdSubmission.gtd_req_id == "N9T49U")).one()
    assert d.owner.name == "Meera S." and d.business_unit.name == "BANKING"
    assert (d.practice, d.grade, d.region, d.status) == ("TES-FS", "C2", "CA", "coverage_required")
    assert d.name == "Banking Shared Services Mainframes - Senior"


def test_admin_owner_needs_a_bu_for_created_demand(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build())
    r = row(db, "N9T49U")
    res = team.post(f"/reconciliation/rows/{r.id}/create", data={"owner_id": str(user_id("kavya"))})
    assert (
        "err=Pick+the+business+unit" in res.headers["location"] or "err=Pick%20the" in res.headers["location"]
    )
    team.post(
        f"/reconciliation/rows/{r.id}/create",
        data={"owner_id": str(user_id("kavya")), "bu_id": str(bu_id("DATA"))},
    )
    db.expire_all()
    assert row(db, "N9T49U").outcome == "confirmed"


def test_manual_match_to_demand_with_mistyped_id_chains_it(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build())
    old = by_ref(db, "DM-000148")  # linked to EKT8HQ, now missing
    r = row(db, "N9T49U")
    team.post(f"/reconciliation/rows/{r.id}/confirm", data={"demand_id": str(old.id)})
    d = by_ref(db, "DM-000148")
    assert d.gtd_req_id == "N9T49U" and d.status == "coverage_required"
    assert d.submissions[0].previous_submission_id == d.submissions[1].id


def test_cannot_act_on_matched_rows_or_other_demands(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build())
    matched = row(db, "2ZT7KP")
    res = team.post(
        f"/reconciliation/rows/{matched.id}/confirm", data={"demand_id": str(by_ref(db, "DM-000151").id)}
    )
    assert "already+matched" in res.headers["location"] or "already%20matched" in res.headers["location"]
    r = row(db, "N9T49U")
    res = team.post(
        f"/reconciliation/rows/{r.id}/confirm", data={"demand_id": str(by_ref(db, "DM-000142").id)}
    )
    assert "already+has+a+row" in res.headers["location"] or "already%20has" in res.headers["location"]


# --- Edge cases -----------------------------------------------------------------------------------


def test_prefix_pointing_at_linked_demand_is_a_conflict(team: Client, db: Session) -> None:
    rows = sample_sheet.ROWS + [
        ("Priya N.", "ZZ9ZZ9", "[DM-000142] Senior Java", "CCA-FS", "D1", "US", date(2026, 10, 27), "0", None,
         "Coverage Required", "Work in Progress"),
    ]  # fmt: skip
    upload(team, sample_sheet.build(rows))
    r = row(db, "ZZ9ZZ9")
    assert r.outcome == "conflict" and "already linked to 2ZT7KP" in (r.note or "")


def test_duplicate_requisition_rows(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build(sample_sheet.ROWS + [sample_sheet.ROWS[0]]))
    rows = db.scalars(
        select(ExcelRow).where(ExcelRow.gtd_req_id == "2ZT7KP").order_by(ExcelRow.row_number)
    ).all()
    assert [r.outcome for r in rows] == ["matched", "duplicate"]


def test_unmapped_status_leaves_demand_linked_with_warning(team: Client, db: Session) -> None:
    rows = [r if r[1] != "8NTHV6" else (*r[:9], "Brand New Status", "Something") for r in sample_sheet.ROWS]
    by_ref(db, "DM-000146").status = "sent_to_gtd"
    db.commit()
    upload(team, sample_sheet.build(rows))
    assert by_ref(db, "DM-000146").status == "linked"
    assert any("Something / Brand New Status" in w for w in latest(db).summary["warnings"])


def test_within_grace_period_is_awaiting(team: Client, db: Session) -> None:
    team.post(f"/gtd-queue/{by_ref(db, 'DM-000149').id}/link", data={"gtd_req_id": "NEW001"})
    upload(team, sample_sheet.build())
    s = latest(db).summary
    assert "DM-000149" in s["awaiting"] and "DM-000149" not in s["missing"]
    assert by_ref(db, "DM-000149").status == "sent_to_gtd"


def test_dropped_only_for_open_demands(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build(), TODAY - timedelta(days=1))
    upload(team, sample_sheet.build(sample_sheet.without("8NTHV6", "3HQK9W")), TODAY)
    s = latest(db).summary
    assert s["dropped"] == ["DM-000146"]
    assert by_ref(db, "DM-000146").status == "dropped"
    esc = db.scalars(select(Escalation).where(Escalation.demand_id == by_ref(db, "DM-000146").id)).one()
    assert esc.type == "dropped"
    assert by_ref(db, "DM-000110").status == "cancelled"  # finished: leaves the sheet quietly
    assert not db.scalars(select(Escalation).where(Escalation.demand_id == by_ref(db, "DM-000110").id)).all()


def test_reappearing_demand_takes_its_stage_back(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build(), TODAY - timedelta(days=2))
    upload(team, sample_sheet.build(sample_sheet.without("8NTHV6")), TODAY - timedelta(days=1))
    upload(team, sample_sheet.build(title=False), TODAY)  # different bytes from the first upload
    assert by_ref(db, "DM-000146").status == "coverage_required"


def test_older_sheet_needs_confirmation(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build(), TODAY)
    older = sample_sheet.build(sample_sheet.without("W3NX5A"))
    r = upload(team, older, TODAY - timedelta(days=3))
    assert r.status_code == 400 and "older than the latest import" in r.text  # type: ignore[attr-defined]
    assert "Import this older sheet anyway" in r.text  # type: ignore[attr-defined]
    r = upload(team, older, TODAY - timedelta(days=3), confirm_older="1")
    assert r.status_code == 303  # type: ignore[attr-defined]


def test_same_file_twice_is_refused(team: Client) -> None:
    data = sample_sheet.build()
    upload(team, data)
    r = upload(team, data, TODAY + timedelta(days=1))
    assert r.status_code == 400 and "already imported" in r.text  # type: ignore[attr-defined]


def test_only_latest_import_is_reconciled(team: Client, db: Session) -> None:
    upload(team, sample_sheet.build(), TODAY - timedelta(days=1))
    first = latest(db)
    upload(team, sample_sheet.build(sample_sheet.without("W3NX5A")), TODAY)
    r = team.post(f"/imports/{first.id}/rerun")
    assert "err=" in r.headers["location"]
    with pytest.raises(reconcile_service.ReconcileError):
        reconcile_service.reconcile(db, db.get_one(ExcelImport, first.id), user_id("farah"))


def test_bad_uploads(team: Client) -> None:
    assert "isn&#39;t an Excel" in upload(team, b"hello").text  # type: ignore[attr-defined]
    r = team.post("/imports", data={"sheet_date": ""}, files={"file": ("notes.csv", b"a,b", "text/csv")})
    assert r.status_code == 400 and ".xlsx" in r.text


def test_renamed_column_via_settings(client: Client, db: Session) -> None:
    headers = [h if h != "Code Requisition" else "GTD Req Code" for h in sample_sheet.HEADERS]
    data = sample_sheet.build(headers=headers)
    client.as_user("farah")
    assert upload(client, data).status_code == 400  # type: ignore[attr-defined]
    form = {f"col_{f}": v for f, v in CFG.dp_columns.items()} | {
        "col_req_id": "GTD Req Code",
        "blank_values": "0, -",
    }
    client.as_user("anil").post("/settings/dp-columns", data=form)
    db.expire_all()
    assert db.get_one(Account, 1).settings.dp_columns["req_id"] == "GTD Req Code"
    assert upload(client.as_user("farah"), data).status_code == 303  # type: ignore[attr-defined]


def test_dp_columns_setting_validation(client: Client) -> None:
    form = {f"col_{f}": v for f, v in CFG.dp_columns.items()} | {"col_status": "", "blank_values": ""}
    r = client.as_user("anil").post("/settings/dp-columns", data=form)
    assert "err=" in r.headers["location"]
    form = {f"col_{f}": v for f, v in CFG.dp_columns.items()} | {"col_status": "Status Group"}
    r = client.post("/settings/dp-columns", data=form)
    assert "only+one+field" in r.headers["location"] or "only%20one" in r.headers["location"]


@pytest.mark.parametrize("who", ["priya", "sanjay", "vikram"])
def test_only_admin_roles_import(client: Client, who: str) -> None:
    client.as_user(who)
    assert client.get("/imports").status_code == 403
    assert client.get("/reconciliation").status_code == 403
    assert upload(client, sample_sheet.build()).status_code == 403  # type: ignore[attr-defined]


def test_upload_keeps_the_file(team: Client, db: Session) -> None:
    data = sample_sheet.build()
    upload(team, data, name="Discover_NA_PSCM_Coverage_Summary_24-Sep-2026.xlsx")
    imp = latest(db)
    got = team.get(f"/imports/{imp.id}/file")
    assert got.status_code == 200 and got.content == data
    assert imp.row_count == len(sample_sheet.ROWS) and imp.file_sha256


def test_import_service_rejects_empty_sheet(team: Client) -> None:
    r = upload(team, sample_sheet.build([]))
    assert r.status_code == 400 and "no data rows" in r.text  # type: ignore[attr-defined]
