"""BCM sheet upload: validate, keep the file, snapshot every row, then reconcile.

Every upload must be the full sheet (a filtered export would make demands look dropped). An older
sheet than the latest is refused unless the uploader confirms, and the exact same file can't be
imported twice (use "Re-run reconciliation" instead).
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import storage
from app.core.security import Actor
from app.models import Account, ExcelImport, ExcelRow, User
from app.services import excel_parser, reconcile_service
from app.services.excel_parser import SheetError

SHEET_EXTENSIONS = {".xlsx"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                      "nov", "dec"], start=1)}  # fmt: skip


class SheetImportError(ValueError):
    """Upload refused. `needs_confirm` means an older sheet the uploader may still confirm."""

    def __init__(self, message: str, needs_confirm: bool = False) -> None:
        super().__init__(message)
        self.needs_confirm = needs_confirm


@dataclass
class ImportResult:
    imp: ExcelImport
    summary: reconcile_service.Summary


def date_from_filename(name: str) -> date | None:
    """'Discover_NA_PSCM_Coverage_Summary_9-Sep-2026.xlsx' → 2026-09-09."""
    m = re.search(r"(\d{1,2})[-_ ]([A-Za-z]{3})[A-Za-z]*[-_ ](\d{4})", name)
    if m and m.group(2).lower() in MONTHS:
        try:
            return date(int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", name)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def import_sheet(
    db: Session, actor: Actor, filename: str, data: bytes, sheet_date: date, *, confirm_older: bool = False
) -> ImportResult:
    try:
        storage.check(filename, data, SHEET_EXTENSIONS)
    except storage.StorageError as e:
        raise SheetImportError(f"BCM sheet not imported: {e}.") from e
    account = db.get_one(Account, actor.account_id)
    try:
        parsed = excel_parser.parse(data, account.settings)
    except SheetError as e:
        raise SheetImportError(str(e)) from e

    digest = hashlib.sha256(data).hexdigest()
    same = db.scalar(
        select(ExcelImport).where(ExcelImport.account_id == account.id, ExcelImport.file_sha256 == digest)
    )
    if same is not None:
        raise SheetImportError(
            f"This exact file was already imported on {same.imported_at:%d %b %Y}. "
            "To match again, use Re-run reconciliation on the latest import."
        )
    latest = reconcile_service.latest_import(db, account.id)
    if latest and latest.sheet_date and sheet_date < latest.sheet_date and not confirm_older:
        raise SheetImportError(
            f"This sheet is dated {sheet_date:%d %b %Y}, older than the latest import "
            f"({latest.sheet_date:%d %b %Y}). Importing it would compare against newer data and could flag "
            "demands as dropped. Tick the box to import it anyway.",
            needs_confirm=True,
        )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    key = storage.save(f"dp/{account.id}/{stamp}", filename, data, SHEET_EXTENSIONS)
    imp = ExcelImport(
        account_id=account.id, file_path=key, file_name=storage.safe_name(filename), sheet_date=sheet_date,
        file_sha256=digest, uploaded_by=actor.id, row_count=len(parsed.rows),
        summary={"parse_warnings": parsed.warnings},
    )  # fmt: skip
    db.add(imp)
    db.flush()
    for r in parsed.rows:
        v = r.values
        db.add(
            ExcelRow(
                import_id=imp.id,
                row_number=r.row_number,
                gtd_req_id=v.get("req_id"),
                demand_request_name=v.get("demand_request_name"),
                status=v.get("status"),
                status_group=v.get("status_group"),
                source=v.get("source"),
                candidate_name=v.get("candidate_name"),
                doj=v.get("doj"),
                originator=v.get("originator"),
                practice=v.get("practice"),
                grade=v.get("grade"),
                region=v.get("region"),
                start_date=v.get("start_date"),
                gettalent_req_id=v.get("gettalent_req_id"),
                candidate_details=v.get("candidate_details"),
                raw=r.raw,
            )  # fmt: skip
        )
    db.flush()
    summary = reconcile_service.reconcile(db, imp, actor.id)
    return ImportResult(imp, summary)


def history(db: Session, account_id: int) -> list[tuple[ExcelImport, str]]:
    imports = list(
        db.scalars(
            select(ExcelImport)
            .where(ExcelImport.account_id == account_id)
            .order_by(ExcelImport.imported_at.desc(), ExcelImport.id.desc())
        )
    )
    uploaders = {i.uploaded_by for i in imports}
    names: dict[int, str] = {
        uid: name for uid, name in db.execute(select(User.id, User.name).where(User.id.in_(uploaders)))
    }
    return [(i, names.get(i.uploaded_by, "—")) for i in imports]
