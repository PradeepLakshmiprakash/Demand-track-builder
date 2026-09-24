"""In-process jobs (APScheduler).

daily_admin_mail runs every minute and sends each account's mail once its local mail time has passed
and no mail went out today. Checking every minute (instead of one cron per account) picks up mail-time
changes from Account settings without a restart and catches up after downtime.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select

from app.core.db import new_session
from app.models import Account
from app.services import notify_service

log = logging.getLogger("demand_tracker.jobs")


def daily_admin_mail() -> None:
    with new_session() as db:
        for account in db.scalars(select(Account).where(Account.active)):
            try:
                if notify_service.is_due(db, account):
                    result = notify_service.send_daily_admin_mail(db, account.id)
                    log.info("daily_admin_mail %s: %s", account.name, result.message)
            except Exception:
                db.rollback()
                log.exception("daily_admin_mail failed for %s", account.name)


def start() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        daily_admin_mail, "interval", minutes=1, id="daily_admin_mail", coalesce=True, max_instances=1
    )
    scheduler.start()
    return scheduler
