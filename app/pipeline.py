"""Pipeline giornaliera: cosa succede dopo che i dati sono arrivati da Garmin.

    sync → readiness + insights → coaching → notifiche

Ogni passo è isolato: se uno fallisce, gli altri proseguono e l'errore finisce
nel risultato. Serve sia allo scheduler notturno sia al bottone "Sincronizza".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import queries as q
from app.ai import usage
from app.ai.insights import top_insights
from app.ai.provider import get_provider
from app.ai.readiness import compute_readiness
from app.db.models import DailyCoachCache, User
from app.goals import active_goal
from app.garmin import service
from app.garmin.client import GarminClientError
from app.garmin.sync import sync_all

logger = logging.getLogger(__name__)

MAX_SYNC_FAILURES_BEFORE_ALERT = 3


@dataclass
class PipelineResult:
    """Esito della pipeline per un utente."""

    user_id: int
    synced: dict[str, int] | None = None
    readiness: int | None = None
    insights: int = 0
    xp: int = 0
    streak: int = 0
    notifications: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# --------------------------- passi ---------------------------

def do_sync(db: Session, user: User, result: PipelineResult) -> None:
    """Scarica i dati da Garmin e aggiorna lo stato di sync dell'utente."""
    try:
        result.synced = sync_all(db, user)
        service.clear_cache(user.id)
        user.last_sync_at = datetime.utcnow()
        user.sync_failures = 0
        db.commit()
    except GarminClientError as exc:
        _record_sync_failure(db, user)
        result.errors.append(f"sync: {exc}")
    except Exception as exc:  # noqa: BLE001
        _record_sync_failure(db, user)
        logger.exception("Sync fallita per utente %s", user.id)
        result.errors.append(f"sync: {exc}")


def _record_sync_failure(db: Session, user: User) -> None:
    """Incrementa il contatore dei fallimenti.

    Il rollback e' obbligatorio: se l'errore veniva dal database, su Postgres
    la transazione resta avvelenata e ogni comando successivo fallisce finche'
    non si annulla. SQLite e' piu' permissivo, quindi il problema si vede solo
    in produzione.
    """
    try:
        db.rollback()
        user.sync_failures = (user.sync_failures or 0) + 1
        db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("Impossibile registrare il fallimento di sync")
        db.rollback()


def refresh_daily_cache(
    db: Session, user: User, day: date | None = None, force_ai: bool = False
) -> DailyCoachCache:
    """Ricalcola readiness e insight del giorno e li salva in cache.

    Readiness e insight sono deterministici: si ricalcolano sempre, non costano
    nulla. Il messaggio del coach passa dall'AI **una sola volta al giorno** —
    se la riga di oggi è già stata scritta dall'AI non si richiama il modello.
    """
    day = day or date.today()
    snap = q.coach_snapshot(db, user.id)
    readiness = compute_readiness(snap)
    insights = top_insights(snap, 3)
    goal = active_goal(db, user.id)

    row = db.scalar(
        select(DailyCoachCache).where(
            DailyCoachCache.user_id == user.id, DailyCoachCache.day == day
        )
    )
    is_new = row is None
    if is_new:
        row = DailyCoachCache(user_id=user.id, day=day)

    # L'AI parla una volta al giorno: se ha già scritto, si riusa il testo.
    already_written_by_ai = row.source == "ai" and not force_ai
    provider = get_provider()
    coaching = provider.coach(snap, readiness, goal) if not already_written_by_ai else None

    row.readiness_score = readiness.score
    row.readiness_label = readiness.label
    row.insights_json = [
        {"icon": i.icon, "title": i.title, "text": i.text, "color": i.color} for i in insights
    ]

    if coaching is not None:
        row.coach_message = coaching.message
        row.source = coaching.source
        row.workout_json = {
            "icon": coaching.workout.icon,
            "type": coaching.workout.type,
            "duration": coaching.workout.duration,
            "hr_zone": coaching.workout.hr_zone,
            "note": coaching.workout.note,
        }
        if coaching.source == "ai" and (coaching.tokens_in or coaching.tokens_out):
            usage.record(
                db, user.id, "coach",
                model=coaching.model,
                tokens_in=coaching.tokens_in,
                tokens_out=coaching.tokens_out,
            )

    row.generated_at = datetime.utcnow()

    if is_new:
        db.add(row)
    db.commit()
    return row


def do_coaching(db: Session, user: User, result: PipelineResult) -> None:
    """Aggiorna readiness/insight, e il messaggio AI quando è configurato."""
    try:
        row = refresh_daily_cache(db, user)
        result.readiness = int(row.readiness_score) if row.readiness_score is not None else None
        result.insights = len(row.insights_json or [])
    except Exception as exc:  # noqa: BLE001
        logger.exception("Coaching fallito per utente %s", user.id)
        result.errors.append(f"coaching: {exc}")


def do_gamification(db: Session, user: User, result: PipelineResult) -> None:
    """Ricalcola XP, serie e traguardi. Deterministico, dai dati appena arrivati."""
    try:
        from app import gamification

        state = gamification.recompute(db, user)
        result.xp = state.total_xp
        result.streak = state.current_streak
    except Exception as exc:  # noqa: BLE001
        logger.exception("Gamification fallita per utente %s", user.id)
        result.errors.append(f"gamification: {exc}")


def do_notifications(db: Session, user: User, result: PipelineResult) -> None:
    """Valuta le regole di notifica e invia sui canali scelti dall'utente."""
    try:
        from app.notifications.dispatcher import dispatch_for_user

        result.notifications = dispatch_for_user(db, user)
    except ModuleNotFoundError:
        pass  # canale notifiche non ancora installato
    except Exception as exc:  # noqa: BLE001
        logger.exception("Notifiche fallite per utente %s", user.id)
        result.errors.append(f"notifiche: {exc}")


# --------------------------- orchestrazione ---------------------------

def run_for_user(db: Session, user: User, *, with_sync: bool = True) -> PipelineResult:
    """Esegue la pipeline completa per un utente."""
    result = PipelineResult(user_id=user.id)
    if with_sync:
        do_sync(db, user, result)
    do_coaching(db, user, result)
    do_gamification(db, user, result)
    do_notifications(db, user, result)
    logger.info(
        "Pipeline utente %s: sync=%s readiness=%s insight=%s notifiche=%s errori=%s",
        user.id, result.synced, result.readiness, result.insights,
        result.notifications, result.errors or "nessuno",
    )
    return result


def active_users(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.is_active.is_(True)).order_by(User.id)).all())


def run_for_all(db: Session) -> list[PipelineResult]:
    """Pipeline per tutti gli utenti attivi. Un errore su uno non blocca gli altri."""
    results = []
    for user in active_users(db):
        try:
            results.append(run_for_user(db, user))
        except Exception as exc:  # noqa: BLE001
            logger.exception("Pipeline interrotta per utente %s", user.id)
            results.append(PipelineResult(user_id=user.id, errors=[str(exc)]))
    return results
