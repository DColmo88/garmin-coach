"""Quando vale la pena disturbare l'utente.

Tutte le regole sono deterministiche: nessuna chiamata AI, nessun costo.
Ogni regola guarda lo snapshot già calcolato e decide se c'è qualcosa che
merita una notifica.

Il principio: una notifica che non cambia il comportamento è spam. Le soglie
sono volutamente severe, e la deduplica (`cooldown_days`) impedisce che lo
stesso avviso arrivi ogni mattina per una settimana.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.readiness import ReadinessResult
from app.db.models import NotificationLog, User, UserGoal
from app.goals import days_to_target


@dataclass(frozen=True)
class EventType:
    """Un tipo di notifica: la sua identità, il testo di default, la cadenza."""

    key: str
    label: str          # come appare nelle preferenze
    description: str    # cosa comporta attivarla
    cooldown_days: int  # non ripetere prima di N giorni
    default_on: bool


EVENT_TYPES: tuple[EventType, ...] = (
    EventType(
        "overtraining", "Rischio di sovrallenamento",
        "Quando il carico acuto supera nettamente quello a cui sei abituato.",
        cooldown_days=3, default_on=True,
    ),
    EventType(
        "readiness_low", "Prontezza molto bassa",
        "Quando il corpo chiede riposo e insistere sarebbe controproducente.",
        cooldown_days=2, default_on=True,
    ),
    EventType(
        "readiness_high", "Giornata da sessione chiave",
        "Quando sei al massimo e vale la pena sfruttarlo.",
        cooldown_days=3, default_on=False,
    ),
    EventType(
        "sleep_decline", "Sonno in peggioramento",
        "Quando la qualità del sonno cala in modo consistente per più notti.",
        cooldown_days=5, default_on=True,
    ),
    EventType(
        "race_countdown", "Gara vicina",
        "Promemoria di scarico nella settimana prima della gara.",
        cooldown_days=2, default_on=True,
    ),
    EventType(
        "sync_failed", "Sincronizzazione non riuscita",
        "Quando l'app non riesce più a scaricare i dati da Garmin.",
        cooldown_days=2, default_on=True,
    ),
    EventType(
        "weekly_summary", "Riepilogo settimanale",
        "La domenica sera, com'è andata la settimana.",
        cooldown_days=6, default_on=False,
    ),
)

EVENT_BY_KEY: dict[str, EventType] = {e.key: e for e in EVENT_TYPES}

# Soglie: raccolte qui perché sono la parte che si vorrà tarare col tempo.
LOAD_RATIO_ALERT = 1.5
READINESS_LOW = 35
READINESS_HIGH = 85
SLEEP_DECLINE_POINTS = 12
SLEEP_FLOOR = 65
RACE_TAPER_DAYS = 7
SYNC_FAILURES_ALERT = 3


@dataclass
class Notification:
    """Una notifica pronta da spedire."""

    event_type: str
    title: str
    body: str
    url: str = "/coach"


# --------------------------- regole ---------------------------


def _overtraining(snap: dict, readiness: ReadinessResult, user: User,
                  goal: UserGoal | None) -> Notification | None:
    ratio = snap.get("load_ratio")
    if ratio is None or ratio < LOAD_RATIO_ALERT:
        return None
    return Notification(
        "overtraining",
        "Carico troppo alto",
        f"Il tuo carico delle ultime settimane è {ratio:.1f} volte quello a cui sei "
        "abituato. È la soglia oltre la quale gli infortuni diventano probabili: "
        "prenditi uno o due giorni facili.",
    )


def _readiness_low(snap: dict, readiness: ReadinessResult, user: User,
                   goal: UserGoal | None) -> Notification | None:
    if readiness.score is None or readiness.score > READINESS_LOW:
        return None
    weak = [f.name.lower() for f in readiness.breakdown if f.color == "red"]
    causa = f" Pesano soprattutto {' e '.join(weak)}." if weak else ""
    return Notification(
        "readiness_low",
        f"Prontezza {readiness.score}/100",
        f"Oggi il corpo chiede riposo.{causa} Un giorno di scarico adesso vale "
        "più di un allenamento fatto male.",
    )


def _readiness_high(snap: dict, readiness: ReadinessResult, user: User,
                    goal: UserGoal | None) -> Notification | None:
    if readiness.score is None or readiness.score < READINESS_HIGH:
        return None
    return Notification(
        "readiness_high",
        f"Prontezza {readiness.score}/100",
        "Sei al massimo: se avevi una sessione impegnativa in programma, oggi è "
        "il giorno giusto.",
    )


def _sleep_decline(snap: dict, readiness: ReadinessResult, user: User,
                   goal: UserGoal | None) -> Notification | None:
    week = snap.get("sleep_score_7d_avg")
    if week is None or week >= SLEEP_FLOOR:
        return None
    return Notification(
        "sleep_decline",
        "Il sonno è calato",
        f"Media di {round(week)} sullo score del sonno nell'ultima settimana. "
        "Tutto il resto — recupero, prontezza, umore — passa da lì.",
        url="/sleep",
    )


def _race_countdown(snap: dict, readiness: ReadinessResult, user: User,
                    goal: UserGoal | None) -> Notification | None:
    if goal is None:
        return None
    days = days_to_target(goal)
    if days is None or not 0 <= days <= RACE_TAPER_DAYS:
        return None
    if days == 0:
        return Notification(
            "race_countdown", f"Oggi: {goal.title}",
            "È il giorno. Non serve altro che quello che hai già costruito.",
            url="/goals",
        )
    return Notification(
        "race_countdown",
        f"{goal.title} tra {days} " + ("giorno" if days == 1 else "giorni"),
        "Settimana di scarico: riduci il volume, mantieni un po' di intensità, "
        "non aggiungere niente di nuovo.",
        url="/goals",
    )


def _sync_failed(snap: dict, readiness: ReadinessResult, user: User,
                 goal: UserGoal | None) -> Notification | None:
    if (user.sync_failures or 0) < SYNC_FAILURES_ALERT:
        return None
    return Notification(
        "sync_failed",
        "Non riesco a leggere i tuoi dati Garmin",
        f"{user.sync_failures} tentativi falliti di seguito. Di solito è la "
        "password cambiata: rifai l'accesso all'app.",
        url="/login",
    )


Rule = Callable[[dict, ReadinessResult, User, UserGoal | None], Notification | None]

RULES: tuple[Rule, ...] = (
    _sync_failed,       # per primo: se i dati non arrivano, il resto non ha senso
    _overtraining,
    _readiness_low,
    _race_countdown,
    _sleep_decline,
    _readiness_high,
)


# --------------------------- preferenze e deduplica ---------------------------


def default_prefs() -> dict[str, bool]:
    return {e.key: e.default_on for e in EVENT_TYPES}


def is_enabled(user: User, event_type: str) -> bool:
    """True se l'utente vuole ricevere questo tipo di notifica."""
    prefs = (user.notify_prefs_json or {}).get("events")
    if not prefs:
        event = EVENT_BY_KEY.get(event_type)
        return event.default_on if event else False
    return bool(prefs.get(event_type, False))


def recently_sent(db: Session, user_id: int, event_type: str) -> bool:
    """True se lo stesso avviso è già partito da poco."""
    event = EVENT_BY_KEY.get(event_type)
    if event is None:
        return False
    since = datetime.utcnow() - timedelta(days=event.cooldown_days)
    return db.scalar(
        select(NotificationLog.id).where(
            NotificationLog.user_id == user_id,
            NotificationLog.event_type == event_type,
            NotificationLog.sent_at >= since,
            NotificationLog.ok.is_(True),
        ).limit(1)
    ) is not None


def evaluate(
    db: Session,
    user: User,
    snap: dict,
    readiness: ReadinessResult,
    goal: UserGoal | None = None,
    max_notifications: int = 1,
) -> list[Notification]:
    """Le notifiche da mandare adesso, già filtrate per preferenze e cooldown.

    Di default ne esce **una sola**: le regole sono ordinate per urgenza e
    ricevere tre avvisi insieme la mattina è il modo più rapido per far
    disattivare tutto.
    """
    out: list[Notification] = []
    for rule in RULES:
        if len(out) >= max_notifications:
            break
        notification = rule(snap, readiness, user, goal)
        if notification is None:
            continue
        if not is_enabled(user, notification.event_type):
            continue
        if recently_sent(db, user.id, notification.event_type):
            continue
        out.append(notification)
    return out


def weekly_summary(
    db: Session, user: User, snap: dict, today: date | None = None
) -> Notification | None:
    """Riepilogo della domenica sera, se l'utente lo ha chiesto."""
    today = today or date.today()
    if today.weekday() != 6:  # 6 = domenica
        return None
    if not is_enabled(user, "weekly_summary"):
        return None
    if recently_sent(db, user.id, "weekly_summary"):
        return None

    parts = []
    if snap.get("consecutive_active_days"):
        parts.append(f"{snap['consecutive_active_days']} giorni attivi di fila")
    if snap.get("sleep_score_7d_avg") is not None:
        parts.append(f"sonno medio {round(snap['sleep_score_7d_avg'])}")
    if snap.get("resting_hr_7d_avg") is not None:
        parts.append(f"FC a riposo {round(snap['resting_hr_7d_avg'])}")

    return Notification(
        "weekly_summary",
        "La tua settimana",
        ", ".join(parts) + "." if parts else "Settimana senza dati registrati.",
    )
