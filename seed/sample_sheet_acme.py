"""A made-up DP sheet in Acme Insurance's own format, to prove the import reads any layout through the
account's column settings: different header names, a different column order, no title row, and "N/A"
for empty cells. `build_sheet` works for any account's columns (the Phase 8 exit test uses it too).

python -m seed.sample_sheet_acme  →  seed/sample_dp_sheet_acme.xlsx
"""

import io
from datetime import date, datetime
from pathlib import Path
from typing import Any

import openpyxl

from seed import acme

# Acme's column order differs from Discover's; the importer matches by header text, not position.
ORDER = [
    "req_id",
    "status",
    "status_group",
    "demand_request_name",
    "practice",
    "grade",
    "region",
    "start_date",
    "originator",
    "source",
    "candidate_name",
    "doj",
    "candidate_details",
    "gettalent_req_id",
]

ROWS: list[dict[str, Any]] = [
    {
        "req_id": "AC1001",
        "demand_request_name": "[DM-000101] Claims Platform Java Engineer",
        "status": "Screening",
        "status_group": "Pipeline",
        "practice": "APP-ENG",
        "grade": "L4",
        "start_date": date(2026, 11, 2),
        "originator": "Lena W.",
        "candidate_name": "Candidate P",
    },
    {
        "req_id": "AC1002",
        "demand_request_name": "[DM-000102] Claims Data Engineer (Spark)",
        "status": "Sourcing",
        "status_group": "Pipeline",
        "practice": "DATA-ENG",
        "grade": "L3",
        "start_date": date(2026, 11, 9),
        "originator": "Lena W.",
    },
    {
        "req_id": "AC1003",
        "demand_request_name": "[DM-000103] Policy Admin QA Automation Lead",
        "status": "Offer Pending",
        "status_group": "Offer",
        "practice": "QA-AUTO",
        "grade": "L5",
        "start_date": date(2026, 10, 19),
        "originator": "Marco B.",
        "source": "Partner",
        "candidate_name": "Candidate Q",
    },
    {
        "req_id": "AC1005",
        "demand_request_name": "[DM-000105] Digital Mobile Engineer",
        "status": "Offer Accepted",
        "status_group": "Offer",
        "practice": "APP-ENG",
        "grade": "L4",
        "start_date": date(2026, 10, 26),
        "originator": "Grace H.",
        "source": "Internal",
        "candidate_name": "Candidate R",
        "doj": date(2026, 11, 2),
    },
    # raised outside the app
    {
        "req_id": "AC9001",
        "demand_request_name": "Claims Fraud Analyst",
        "status": "Sourcing",
        "status_group": "Pipeline",
        "practice": "DATA-ENG",
        "grade": "L4",
        "start_date": date(2026, 12, 1),
        "originator": "Lena W.",
    },
]


def build_sheet(
    columns: dict[str, str], rows: list[dict[str, Any]], *, order: list[str] | None = None, blank: str = "N/A"
) -> bytes:
    """An .xlsx with the given header text per field; cells a row doesn't set are written as `blank`."""
    fields = order or list(columns)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append([columns[f] for f in fields])
    for r in rows:
        ws.append(
            [
                datetime.combine(v, datetime.min.time()) if isinstance(v := r.get(f, blank), date) else v
                for f in fields
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build(rows: list[dict[str, Any]] | None = None) -> bytes:
    columns: dict[str, str] = acme.CONFIG["dp_columns"]  # type: ignore[assignment]
    return build_sheet(columns, ROWS if rows is None else rows, order=ORDER)


if __name__ == "__main__":
    out = Path(__file__).with_name("sample_dp_sheet_acme.xlsx")
    out.write_bytes(build())
    print(f"Wrote {out} ({len(ROWS)} rows)")
