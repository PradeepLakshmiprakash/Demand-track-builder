"""Integration points for systems outside the app.

Karat (a separate interview platform, answer of 24 Sep) posts interview results here. This is the
shape the app is ready for; the Karat side, its exact payload and authentication still need
agreeing. Disabled unless KARAT_API_KEY is set; callers send it in the X-Api-Key header.
"""

import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.services import interview_service
from app.services.interview_service import InterviewError

router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class KaratResult(BaseModel):
    external_ref: str = Field(min_length=1, max_length=80, description="Karat's id for this interview")
    candidate_name: str = Field(min_length=1, max_length=160)
    gtd_req_id: str | None = Field(None, description="GTD requisition ID, if Karat knows it")
    round: str = Field("L1", pattern="^L[12]$")
    outcome: str = Field(pattern="^(select|reject|hold)$")
    report_url: str | None = None
    comments: str | None = None


@router.post("/karat/results", status_code=201)
def karat_result(
    body: KaratResult, x_api_key: str | None = Header(None), db: Session = Depends(get_db)
) -> dict[str, object]:
    s = get_settings()
    if not s.karat_api_key:
        raise HTTPException(404, "The Karat integration isn't configured.")
    if not x_api_key or not secrets.compare_digest(x_api_key, s.karat_api_key):
        raise HTTPException(401, "Bad API key.")
    try:
        iv = interview_service.record_external(
            db,
            s.karat_account_id,
            candidate_name=body.candidate_name,
            gtd_req_id=body.gtd_req_id,
            round_=body.round,
            outcome=body.outcome,
            external_ref=body.external_ref,
            report_url=body.report_url,
            comments=body.comments,
        )
    except InterviewError as e:
        raise HTTPException(422, str(e)) from e
    return {"interview_id": iv.id, "mapped_to_requisition": iv.demand_id is not None}
