"""Scheduled jobs when hosted serverless (Vercel Cron): the in-process scheduler can't run there, so the
platform calls this once a day. Locally and on a normal server the APScheduler jobs do the same."""

import secrets

from fastapi import APIRouter, Header, HTTPException

from app.core.config import get_settings
from app.jobs.scheduler import daily_admin_mail, escalation_sweep

router = APIRouter(tags=["cron"])


@router.get("/api/cron/daily", include_in_schema=False)
def daily(authorization: str = Header("")) -> dict[str, str]:
    secret = get_settings().cron_secret
    if not secret or not secrets.compare_digest(authorization, f"Bearer {secret}"):
        raise HTTPException(401, "Not allowed.")
    daily_admin_mail()
    escalation_sweep()
    return {"status": "ok"}
