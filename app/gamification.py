"""XP, livelli, serie e traguardi.

Tutto ricalcolato dai dati dopo ogni sincronizzazione: non c'è stato da
mantenere coerente, e se un giorno arrivano dati in ritardo i conteggi si
sistemano da soli.

Il tono è quello di un allenatore, non di un videogioco: i traguardi premiano
la costanza e il recupero, non solo la fatica.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Activity,
    DailyWellness,
    GamificationState,
    SleepRecord,
    TrainingMetric,
    User,
)

logger = logging.getLogger(__name__)

# Quanto indietro si guarda per ricostruire i punteggi.
WINDOW_DAYS = 365

# --------------------------- punteggi ---------------------------

XP_PER_ACTIVITY = 50
XP_PER_KM = 1
XP_GOOD_SLEEP = 40          # score ≥ 80
XP_STEP_GOAL = 25
XP_ACTIVE_WEEK = 100        # 5+ giorni attivi in una settimana
SLEEP_GOOD_SCORE = 80

LEVELS: tuple[tuple[str, int, str], ...] = (
    ("Principiante", 0, "🌱"),
    ("Atleta", 1_000, "🏅"),
    ("Costante", 5_000, "🥈"),
    ("Elite", 15_000, "🥇"),
    ("Leggenda", 40_000, "🏆"),
)


@dataclass
class Badge:
    key: str
    icon: str
    title: str
    description: str


BADGES: tuple[Badge, ...] = (
    Badge("first_10k", "🏃", "Primi 10 km", "La prima uscita da 10 km o più."),
    Badge("half", "🏅", "Mezza distanza", "Una singola uscita da 21 km o più."),
    Badge("streak_7", "🔥", "Una settimana piena", "7 giorni consecutivi di attività."),
    Badge("streak_30", "⚡", "Un mese di fila", "30 giorni consecutivi di attività."),
    Badge("sleep_week", "🌙", "Sonno in ordine", "7 notti di fila con score 80 o più."),
    Badge("vo2_up", "📈", "Motore più grosso", "VO₂max cresciuto di almeno 2 punti."),
    Badge("early_bird", "🌅", "Mattiniero", "10 allenamenti iniziati prima delle 7."),
    Badge("century", "💯", "100 km", "100 km percorsi in un mese solare."),
    Badge("recovery", "🧘", "Recupero gestito",
          "14 giorni senza mai scendere sotto 30 di Body Battery."),
)

BADGE_BY_KEY = {b.key: b for b in BADGES}


@dataclass
class Progress:
    """Lo stato di gioco di un utente, pronto per la UI."""

    total_xp: int
    level: str
    level_icon: str
    next_level: str | None
    xp_to_next: int
    level_progress_pct: int
    current_streak: int
    longest_streak: int
    badges: list[Badge]


# --------------------------- calcolo ---------------------------


def _level_for(xp: int) -> tuple[str, str, str | None, int, int]:
    """Livello attuale, icona, prossimo livello, XP mancanti, percentuale."""
    current = LEVELS[0]
    following = None
    for index, level in enumerate(LEVELS):
        if xp >= level[1]:
            current = level
            following = LEVELS[index + 1] if index + 1 < len(LEVELS) else None

    if following is None:
        return current[0], current[2], None, 0, 100

    span = following[1] - current[1]
    done = xp - current[1]
    return current[0], current[2], following[0], following[1] - xp, round(100 * done / span)


def _streaks(active_days: set[date], today: date) -> tuple[int, int]:
    """Serie attuale e più lunga di giorni consecutivi con attività.

    La serie di oggi non si spezza se oggi non ti sei ancora allenato: si
    conta a partire da ieri, altrimenti la barra crollerebbe ogni mattina.
    """
    if not active_days:
        return 0, 0

    current = 0
    cursor = today if today in active_days else today - timedelta(days=1)
    while cursor in active_days:
        current += 1
        cursor -= timedelta(days=1)

    longest = 0
    run = 0
    previous: date | None = None
    for day in sorted(active_days):
        run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
        longest = max(longest, run)
        previous = day

    return current, longest


def _earned_badges(
    activities: list[Activity],
    sleeps: list[SleepRecord],
    wellness: list[DailyWellness],
    training: list[TrainingMetric],
    longest_streak: int,
    active_days: set[date],
) -> list[str]:
    earned: list[str] = []

    distances = [(a.distance_m or 0) for a in activities]
    if any(d >= 10_000 for d in distances):
        earned.append("first_10k")
    if any(d >= 21_000 for d in distances):
        earned.append("half")

    if longest_streak >= 7:
        earned.append("streak_7")
    if longest_streak >= 30:
        earned.append("streak_30")

    # 7 notti consecutive con buon sonno
    good_nights = {s.day for s in sleeps if (s.sleep_score or 0) >= SLEEP_GOOD_SCORE}
    if good_nights:
        run = best = 0
        previous: date | None = None
        for day in sorted(good_nights):
            run = run + 1 if previous is not None and day - previous == timedelta(days=1) else 1
            best = max(best, run)
            previous = day
        if best >= 7:
            earned.append("sleep_week")

    vo2 = [t.vo2max for t in training if t.vo2max is not None]
    if len(vo2) >= 2 and max(vo2) - min(vo2) >= 2:
        earned.append("vo2_up")

    early = [a for a in activities if a.start_time and a.start_time.hour < 7]
    if len(early) >= 10:
        earned.append("early_bird")

    # 100 km in un mese solare
    by_month: dict[str, float] = {}
    for activity in activities:
        if activity.start_time:
            key = activity.start_time.strftime("%Y-%m")
            by_month[key] = by_month.get(key, 0) + (activity.distance_m or 0)
    if any(metres >= 100_000 for metres in by_month.values()):
        earned.append("century")

    # 14 giorni consecutivi senza scendere sotto 30 di Body Battery
    lows = {w.day: w.body_battery_low for w in wellness if w.body_battery_low is not None}
    if lows:
        run = best = 0
        previous = None
        for day in sorted(lows):
            consecutive = previous is not None and day - previous == timedelta(days=1)
            run = run + 1 if consecutive and lows[day] >= 30 else (1 if lows[day] >= 30 else 0)
            best = max(best, run)
            previous = day
        if best >= 14:
            earned.append("recovery")

    return earned


def recompute(db: Session, user: User, today: date | None = None) -> GamificationState:
    """Ricalcola tutto dai dati e salva lo stato."""
    today = today or date.today()
    since = today - timedelta(days=WINDOW_DAYS)

    activities = list(db.scalars(
        select(Activity).where(Activity.user_id == user.id).order_by(Activity.start_time)
    ).all())
    sleeps = list(db.scalars(
        select(SleepRecord).where(SleepRecord.user_id == user.id, SleepRecord.day >= since)
    ).all())
    wellness = list(db.scalars(
        select(DailyWellness).where(DailyWellness.user_id == user.id, DailyWellness.day >= since)
    ).all())
    training = list(db.scalars(
        select(TrainingMetric).where(TrainingMetric.user_id == user.id,
                                     TrainingMetric.day >= since)
    ).all())

    # --- XP ---
    total_xp = 0
    total_xp += XP_PER_ACTIVITY * len(activities)
    total_xp += int(XP_PER_KM * sum((a.distance_m or 0) for a in activities) / 1000)
    total_xp += XP_GOOD_SLEEP * sum(1 for s in sleeps if (s.sleep_score or 0) >= SLEEP_GOOD_SCORE)
    total_xp += XP_STEP_GOAL * sum(
        1 for w in wellness
        if w.total_steps and w.step_goal and w.total_steps >= w.step_goal
    )

    active_days = {a.start_time.date() for a in activities if a.start_time}

    # bonus per le settimane con almeno 5 giorni attivi
    by_week: dict[tuple[int, int], int] = {}
    for day in active_days:
        key = day.isocalendar()[:2]
        by_week[key] = by_week.get(key, 0) + 1
    total_xp += XP_ACTIVE_WEEK * sum(1 for count in by_week.values() if count >= 5)

    current_streak, longest_streak = _streaks(active_days, today)
    badges = _earned_badges(activities, sleeps, wellness, training,
                            longest_streak, active_days)

    state = db.scalar(
        select(GamificationState).where(GamificationState.user_id == user.id)
    )
    if state is None:
        state = GamificationState(user_id=user.id)
        db.add(state)

    level, _, _, _, _ = _level_for(total_xp)
    state.total_xp = total_xp
    state.current_level = level
    state.current_streak = current_streak
    # Il record storico non si perde nemmeno se la finestra si sposta.
    state.longest_streak = max(longest_streak, state.longest_streak or 0)
    state.badges_json = badges
    db.commit()
    return state


def progress_of(db: Session, user: User) -> Progress:
    """Lo stato salvato, in forma pronta per la UI."""
    state = db.scalar(
        select(GamificationState).where(GamificationState.user_id == user.id)
    )
    xp = state.total_xp if state else 0
    level, icon, next_level, to_next, pct = _level_for(xp)
    keys = (state.badges_json if state else None) or []

    return Progress(
        total_xp=xp,
        level=level,
        level_icon=icon,
        next_level=next_level,
        xp_to_next=to_next,
        level_progress_pct=pct,
        current_streak=state.current_streak if state else 0,
        longest_streak=state.longest_streak if state else 0,
        badges=[BADGE_BY_KEY[k] for k in keys if k in BADGE_BY_KEY],
    )
