"""Sync automatica giornaliera.

APScheduler gira dentro il processo FastAPI: con una manciata di utenti un
worker separato sarebbe complessità senza guadagno. Se un giorno servisse
scalare, questo è il modulo da sostituire con una coda.

Gli utenti vengono processati a distanza di qualche minuto l'uno dall'altro
(`SYNC_STAGGER_MINUTES`) per non fare raffiche di richieste a Garmin.
"""
from __future__ import annotations

import logging
import threading
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import settings
from app.db.database import SessionLocal
from app.pipeline import active_users, run_for_user

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def run_daily_job() -> None:
    """Esegue la pipeline per ogni utente attivo, con pausa tra un utente e l'altro."""
    db = SessionLocal()
    try:
        users = active_users(db)
        logger.info("Sync giornaliera: %d utenti da processare.", len(users))
        for index, user in enumerate(users):
            if index > 0 and settings.SYNC_STAGGER_MINUTES > 0:
                time.sleep(settings.SYNC_STAGGER_MINUTES * 60)
            try:
                run_for_user(db, user)
            except Exception:  # noqa: BLE001
                logger.exception("Pipeline giornaliera fallita per utente %s", user.id)
    finally:
        db.close()


def start_scheduler() -> BackgroundScheduler | None:
    """Avvia lo scheduler. Restituisce None se disabilitato da configurazione."""
    global _scheduler
    if not settings.SCHEDULER_ENABLED:
        logger.info("Scheduler disabilitato (SCHEDULER_ENABLED=false).")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone=settings.SCHEDULER_TIMEZONE)
    scheduler.add_job(
        run_daily_job,
        CronTrigger(
            hour=settings.SYNC_HOUR,
            minute=settings.SYNC_MINUTE,
            timezone=settings.SCHEDULER_TIMEZONE,
        ),
        id="daily_sync",
        name="Sync giornaliera Garmin + coaching + notifiche",
        replace_existing=True,
        misfire_grace_time=3600,  # se il server era spento, recupera entro un'ora
        coalesce=True,            # niente esecuzioni accumulate da recuperare
        max_instances=1,
    )
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler avviato: sync giornaliera alle %02d:%02d (%s).",
        settings.SYNC_HOUR, settings.SYNC_MINUTE, settings.SCHEDULER_TIMEZONE,
    )
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def next_run_time():
    """Prossima esecuzione programmata, o None se lo scheduler non gira."""
    if _scheduler is None:
        return None
    job = _scheduler.get_job("daily_sync")
    return job.next_run_time if job else None


def trigger_now_in_background(user_id: int) -> None:
    """Lancia la pipeline di un utente fuori dal ciclo di richiesta HTTP."""

    def worker() -> None:
        db = SessionLocal()
        try:
            from app.db.models import User

            user = db.get(User, user_id)
            if user is not None:
                run_for_user(db, user)
        finally:
            db.close()

    threading.Thread(target=worker, daemon=True, name=f"sync-user-{user_id}").start()
