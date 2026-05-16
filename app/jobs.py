from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .config import settings
from .db import SessionLocal
from .services.sync_service import sync_all

scheduler: BackgroundScheduler | None = None


def run_scheduled_sync(send_report: bool = False) -> None:
    db = SessionLocal()
    try:
        sync_all(db, build_report=True, send_report=send_report)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler | None:
    global scheduler
    if scheduler is not None or not settings.scheduler_enabled:
        return scheduler
    scheduler = BackgroundScheduler(timezone=settings.timezone)
    scheduler.add_job(
        run_scheduled_sync,
        CronTrigger(hour=settings.scheduler_morning_hour, minute=0),
        kwargs={'send_report': True},
        id='morning_report',
        replace_existing=True,
    )
    scheduler.add_job(
        run_scheduled_sync,
        CronTrigger(hour=settings.scheduler_evening_hour, minute=0),
        kwargs={'send_report': False},
        id='evening_check',
        replace_existing=True,
    )
    scheduler.start()
    return scheduler
