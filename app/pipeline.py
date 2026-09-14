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
from app.ai.provider import get_provider
from app.ai.readiness import compute_readiness
from app.db.models import DailyCoachCache, User
from app.clock import naive_utc, today_for
from app.goals import active_goal
from app.garmin import service
from app.garmin.client import GarminClientError

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
    """Scarica i dati dalla sorgente collegata e aggiorna lo stato di sync.

    Quale sorgente sia lo decide `app.providers`: qui non si sa più se è Garmin
    o Strava, e va bene così — la pipeline si occupa dell'ordine dei passi, non
    di chi risponde alle richieste.
    """
    from app import providers

    if user.connection is None:
        # Registrato ma non ancora collegato: non è un errore, è il momento
        # prima del primo collegamento. Segnalarlo come guasto farebbe partire
        # le notifiche di sync fallita a chi non ha ancora fatto niente.
        result.synced = {}
        return

    try:
        result.synced = providers.sync_user(db, user)
        service.clear_cache(user.id)
        user.last_sync_at = naive_utc()
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
    """Ricalcola quello che si vede sulla home e lo salva in cache.

    La **prontezza** è deterministica e si ricalcola sempre: è gratis.

    Il **messaggio del coach**, gli **insight** e le **letture delle pagine**
    passano dall'AI, e per loro vale la regola di sempre: una volta al giorno
    per utente. Se la riga di oggi è già stata scritta dall'AI non si richiama
    il modello, altrimenti ogni apertura della home sarebbe una chiamata.

    Tutte e tre condividono lo stesso briefing, costruito una volta sola.

    **L'AI parla solo di oggi.** La regola «una chiamata al giorno» reggeva
    finché il giorno era uno solo; da quando `/coach` accetta `?day=`, ogni
    data mai vista era una riga di cache nuova e quindi tre chiamate al
    modello — due delle quali sul modello grande. Bastava cliccare «giorno
    precedente» venti volte, o un prefetch del browser sui link di
    navigazione, per bruciare il budget del mese. Per le date passate si legge
    quello che c'è in cache e per il resto si mostra il deterministico, che è
    anche l'unica cosa sensata: il coaching di tre giorni fa scritto oggi
    saprebbe com'è andata a finire.
    """
    today = today_for(user)
    day = day or today
    ai_allowed = day == today
    snap = q.coach_snapshot(db, user.id, day)
    readiness = compute_readiness(snap)
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
    coaching = None
    insights = None

    # Seconda rete, indipendente dalla prima: un tetto sulle chiamate di tipo
    # `coach` registrate oggi. La regola «solo il giorno corrente» ne consente
    # una sola in condizioni normali; il tetto serve a contenere i casi che non
    # ho previsto, allo stesso modo in cui la quota della chat contiene i suoi.
    if ai_allowed and not already_written_by_ai:
        try:
            usage.check_quota(db, user, "coach")
        except usage.QuotaExceeded as exc:
            logger.warning("Tetto coaching raggiunto per utente %s: %s", user.id, exc)
            ai_allowed = False

    if ai_allowed and not already_written_by_ai:
        # Il briefing si costruisce solo quando si sta per parlare col modello:
        # è qualche query in più che non ha senso pagare a ogni pageview. Lo
        # stesso testo serve al messaggio del coach **e** agli insight, quindi
        # si costruisce una volta e si passa a entrambi.
        from app.ai import briefing as briefing_builder
        from app.ai import readings as readings_builder
        from app.ai.insights import generate_insights

        context = briefing_builder.build(db, user, day)
        coaching = provider.coach(snap, readiness, goal, briefing=context)
        insights, _ = generate_insights(db, user, briefing=context)
        # Se non risponde resta `None` e le pagine tengono le frasi calcolate:
        # una lettura mancata non deve lasciare una pagina senza intestazione.
        page_readings = readings_builder.generate(db, user, context)
        if page_readings:
            row.readings_json = page_readings

    row.readiness_score = readiness.score
    row.readiness_label = readiness.label

    # La prontezza si ricalcola sempre perché è gratis. Gli insight no: adesso
    # li scrive il modello, e rigenerarli a ogni pageview sarebbe una chiamata
    # a ogni apertura della home.
    if insights is not None:
        row.insights_json = [
            {"title": i.title, "text": i.text, "color": i.color} for i in insights
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

    row.generated_at = naive_utc()

    # Una riga nuova per una data passata non si scrive: sarebbe una riga di
    # cache per ogni data che qualcuno digita nell'indirizzo, e non
    # conterrebbe niente che non si possa ricalcolare in un millesimo di
    # secondo. L'oggetto torna comunque al chiamante, solo non persistito.
    if is_new and not ai_allowed:
        return row

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
