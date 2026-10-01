"""A made-up BCM sheet in the real sheet's shape (same 16 headers, a title row above them, blanks
written as 0 or -), built to reconcile against the seed data. No real names or candidates.

python -m seed.sample_sheet  →  seed/sample_dp_sheet.xlsx

What it exercises when imported on top of the seed:
- 12 rows whose requisition IDs are already linked (tier 1), one with a trailing space in its status;
- EKT8HQ (DM-000148, sent to GTD 9 days ago) left out on purpose → missing + escalation;
- [DM-000147] in a row's name with an unrecorded ID → linked automatically (tier 2);
- a row that looks like DM-000151 but carries no prefix → fuzzy suggestion (tier 3);
- a row raised outside the app (N9T49U) → match or create a demand from it;
- M2PX6D marked "In Correct Demnad" → incorrect demand.
"""

import io
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path

import openpyxl

HEADERS = [
    "ORIGINATOR_NAME", "Code Requisition", "Demand Request Name", "Practice", "Local Grade",
    "GetTalent - Job Req ID", "Sourcer", "External Candidate Details", "Region", "Position Start Date",
    "Demand", "Source", "Candidate Name", "DOJ", "Status", "Status Group",
]  # fmt: skip

# originator, req, name, practice, grade, region, start, source, doj, status, group
Row = tuple[str, str, str, str, str, str, date, str, date | None, str, str]

ROWS: list[Row] = [
    ("Priya N.", "2ZT7KP", "#7-Senior Java Full Stack Developer", "CCA-FS", "D1", "US", date(2026, 10, 27),
     "0", None, "Coverage Required", "Work in Progress"),
    ("Priya N.", "DIT7AF", "01-Senior Salesforce Developer Payments Chicago", "DCX-FS", "C2", "US",
     date(2026, 8, 3), "VMS", None, "Offer in Process", "Offer in Market/Process"),
    ("Priya N.", "0ZTQQE", "*09-Java AWS Developer Payments Chicago", "CCA-FS", "C1", "US", date(2026, 9, 1),
     "FTE", date(2026, 10, 15), "Offer in Market", "Offer in Market/Process"),
    ("Priya N.", "IXT3SF", "*05-Java AWS Developer Payments Chicago", "CCA-FS", "C1", "US", date(2026, 9, 1),
     "Sogeti", date(2026, 10, 15), "Offer in Market", "Offer in Market/Process"),
    ("Priya N.", "43TUIX", "*06-Java AWS Developer Payments Chicago", "CCA-FS", "C1", "US", date(2026, 9, 1),
     "Sogeti", date(2026, 9, 8), "Allocation Pending ", "Staffed"),
    ("Rahul K.", "1UT9PL", "Cards Mainframe Developer", "DMN-FS", "C1", "US", date(2026, 10, 20),
     "0", None, "Coverage Required", "Work in Progress"),
    ("Rahul K.", "BPTONE", "Cards Mainframe Developer", "DMN-FS", "B1", "US", date(2026, 10, 1),
     "Sogeti", None, "Offer in Process", "Offer in Market/Process"),
    ("Neha T.", "D6T03K", "#02-Java Full Stack Developer", "CCA-FS", "C2", "US", date(2026, 10, 27),
     "VMS", None, "CI to be Scheduled", "Profiles with Client"),
    ("Neha T.", "8NTHV6", "#3-Senior Java Full Stack Developer", "CCA-FS", "D1", "US", date(2026, 11, 10),
     "0", None, "Coverage Required", "Work in Progress"),
    ("Meera S.", "Y7TR36", "Banking Shared Services Mainframes", "TES-FS", "C1", "US", date(2026, 9, 4),
     "VMS", None, "CI to be Scheduled", "Profiles with Client"),
    ("Meera S.", "3HQK9W", "Deposits Platform Engineer", "ADM-FS", "C1", "US", date(2026, 9, 15),
     "0", None, "Demand to be Cancelled", "Cancelled/Abandon"),
    ("Arjun D.", "M2PX6D", "Senior Data Engineer", "DMN-FS", "D1", "US", date(2026, 10, 13),
     "0", None, "In Correct Demnad", "0"),
    # [DM-000147] prefix, ID never recorded in the app → tier 2
    ("Arjun D.", "P7KD2M", "[DM-000147] Data Engineer (Spark + Snowflake)", "DMN-FS", "C1", "US",
     date(2026, 11, 3), "0", None, "Coverage Required", "Work in Progress"),
    # looks like DM-000151, no prefix → tier 3 suggestion
    ("Priya N.", "W3NX5A", "Senior Java Full Stack Developer - Java Spring Boot AWS", "CCA-FS", "D1", "US",
     date(2026, 10, 27), "0", None, "Coverage Required", "Work in Progress"),
    # raised outside the app
    ("Meera S.", "N9T49U", "Banking Shared Services Mainframes - Senior", "TES-FS", "C2", "CA",
     date(2026, 11, 24), "0", None, "Coverage Required", "Work in Progress"),
]  # fmt: skip


# Placeholder candidate per requisition (the real sheet's Candidate Name column).
CANDIDATES = {
    "DIT7AF": "Candidate A",
    "BPTONE": "Candidate B",
    "0ZTQQE": "Candidate C",
    "IXT3SF": "Candidate D",
    "43TUIX": "Candidate E",
}


def build(rows: Iterable[Row] = ROWS, *, title: bool = True, headers: list[str] | None = None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "data"
    if title:
        ws.append(["Discover NA PSCM coverage summary (sample)"])
    ws.append(headers or HEADERS)
    for orig, req, name, practice, grade, region, start, source, doj, status, group in rows:
        ws.append(
            [
                orig,
                req,
                name,
                practice,
                grade,
                "-",
                None,
                "Candidate 1: sample line\nCandidate 2: sample line",
                region,
                datetime.combine(start, datetime.min.time()),
                1,
                source,
                CANDIDATES.get(req, 0),
                datetime.combine(doj, datetime.min.time()) if doj else None,
                status,
                group,
            ]
        )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def without(*req_ids: str) -> list[Row]:
    return [r for r in ROWS if r[1] not in req_ids]


if __name__ == "__main__":
    out = Path(__file__).with_name("sample_dp_sheet.xlsx")
    out.write_bytes(build())
    print(f"Wrote {out} ({len(ROWS)} rows)")
