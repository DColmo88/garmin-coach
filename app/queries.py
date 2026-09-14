"""Query di lettura dal DB: serie storiche ordinate per i grafici e le tabelle."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import today_for
from app.analysis import baseline
from app.db.models import (
    Activity,
    BodyComposition,
    DailyCheckin,
    DailyWellness,
    SleepRecord,
    TrainingMetric,
    User,
)


def _asc(rows: list) -> list:
    """Ordina per giorno crescente (per i grafici temporali)."""
    return list(reversed(rows))


def exact_latest(rows: list, attr: str, target_date: date) -> Any:
    """Il valore solo se è **di quel giorno**, altrimenti `None`.

    Resta per chi vuole la lettura secca «il dato di oggi c'è o non c'è».
    Dove serve sapere anche *quanto è vecchio* si usa `latest_with_age`.
    """
    value, age = latest_with_age(rows, attr, target_date)
    return value if age == 0 else None


def latest_with_age(rows: list, attr: str, target_date: date) -> tuple[Any, int | None]:
    """Il valore più recente non oltre `target_date`, e la sua età in giorni.

    La versione binaria — o è di oggi, o non esiste — era nata per una ragione
    giusta: un sonno di tre giorni fa presentato come «stanotte» è una bugia.
    Ma buttarlo via del tutto ne diceva un'altra: chi apriva l'app prima della
    sincronizzazione del mattino, o dopo una notte in cui la sync era fallita,
    si vedeva «Dati insufficienti» pur avendo due mesi di storia.

    Un dato ha un'età. Chi legge decide quanto farla pesare — il motore di
    prontezza la fa decadere e la dichiara — invece di fingere che il dato sia
    di stamattina o che non esista.
    """
    for row in reversed(rows):
        day = getattr(row, "day", None)
        if day is None or day > target_date:
            continue
        value = getattr(row, attr, None)
        if value is not None:
            return value, (target_date - day).days
    return None, None


def recent_activities(db: Session, user_id: int, limit: int = 50, end_date: date | None = None) -> list[Activity]:
    stmt = select(Activity).where(Activity.user_id == user_id)
    if end_date:
        stmt = stmt.where(Activity.start_time < datetime.combine(end_date + timedelta(days=1), datetime.min.time()))
    return list(
        db.scalars(
            stmt.order_by(Activity.start_time.desc()).limit(limit)
        ).all()
    )


def get_activity(db: Session, user_id: int, activity_id: int) -> Activity | None:
    return db.scalar(
        select(Activity).where(
            Activity.user_id == user_id, Activity.external_id == activity_id
        )
    )


def wellness_series(db: Session, user_id: int, days: int = 28, end_date: date | None = None) -> list[DailyWellness]:
    stmt = select(DailyWellness).where(DailyWellness.user_id == user_id)
    if end_date:
        stmt = stmt.where(DailyWellness.day <= end_date)
    rows = db.scalars(stmt.order_by(DailyWellness.day.desc()).limit(days)).all()
    return _asc(list(rows))


def sleep_series(db: Session, user_id: int, days: int = 28, end_date: date | None = None) -> list[SleepRecord]:
    stmt = select(SleepRecord).where(SleepRecord.user_id == user_id)
    if end_date:
        stmt = stmt.where(SleepRecord.day <= end_date)
    rows = db.scalars(stmt.order_by(SleepRecord.day.desc()).limit(days)).all()
    return _asc(list(rows))


def training_series(db: Session, user_id: int, days: int = 28, end_date: date | None = None) -> list[TrainingMetric]:
    stmt = select(TrainingMetric).where(TrainingMetric.user_id == user_id)
    if end_date:
        stmt = stmt.where(TrainingMetric.day <= end_date)
    rows = db.scalars(stmt.order_by(TrainingMetric.day.desc()).limit(days)).all()
    return _asc(list(rows))


def body_series(db: Session, user_id: int, days: int = 90, end_date: date | None = None) -> list[BodyComposition]:
    stmt = select(BodyComposition).where(BodyComposition.user_id == user_id)
    if end_date:
        stmt = stmt.where(BodyComposition.day <= end_date)
    rows = db.scalars(stmt.order_by(BodyComposition.day.desc()).limit(days)).all()
    return _asc(list(rows))


def latest(rows: list, attr: str) -> Any:
    """Ultimo valore non nullo di un attributo in una serie crescente."""
    for row in reversed(rows):
        val = getattr(row, attr, None)
        if val is not None:
            return val
    return None


def labels(rows: list, fmt: str = "%d/%m") -> list[str]:
    return [r.day.strftime(fmt) for r in rows]


def values(rows: list, attr: str) -> list:
    return [getattr(r, attr, None) for r in rows]


def _avg(nums: list) -> float | None:
    vals = [n for n in nums if n is not None]
    return sum(vals) / len(vals) if vals else None


def _is_run(activity_type: str | None) -> bool:
    return bool(activity_type) and "run" in activity_type.lower()


def _training_load(db: Session, user_id: int, target_date: date | None = None):
    """Il quadro del carico calcolato. `None` se l'utente non esiste più.

    L'import sta dentro la funzione perché `app.analysis.summary` importa a sua
    volta i modelli: tenerlo in cima creerebbe un ciclo con questo modulo.
    """
    from app.analysis import cache as analysis_cache

    user = db.get(User, user_id)
    if user is None:
        return None
    return analysis_cache.load_summary(db, user, target_date)


def coach_snapshot(db: Session, user_id: int, target_date: date | None = None) -> dict:
    """Costruisce lo snapshot deterministico per readiness/insights/coaching.

    Tutto None-safe: campi mancanti -> None, mai eccezioni.
    """
    if target_date is None:
        # Il giorno è quello dell'utente: `date.today()` avrebbe risposto col
        # fuso del server, e su questo giorno poggia tutto il resto dello
        # snapshot (i valori «di oggi», le medie, i giorni consecutivi).
        owner = db.get(User, user_id)
        target_date = today_for(owner) if owner is not None else date.today()
    # Sessanta giorni e non trenta: è la finestra su cui si costruisce «il
    # normale di questa persona» (`app.analysis.baseline`), e un percentile su
    # trenta giorni oscilla troppo per essere il fondo di un punteggio.
    window = baseline.WINDOW_DAYS
    wellness = wellness_series(db, user_id, window, end_date=target_date)  # crescente
    sleep = sleep_series(db, user_id, window, end_date=target_date)
    training = training_series(db, user_id, window, end_date=target_date)
    acts = recent_activities(db, user_id, 50, end_date=target_date)     # desc per start_time

    rhr_all = [w.resting_hr for w in wellness[-30:]]
    rhr_7 = [w.resting_hr for w in wellness[-7:]]

    # Il carico non arriva più da `training_load`: per molti account Garmin non
    # lo popola affatto, e un campo vuoto faceva restituire sempre lo stesso
    # valore neutro. Si ricalcola dalle attività (vedi `app.analysis`).
    load = _training_load(db, user_id, target_date)

    latest_sleep = sleep[-1] if sleep else None
    deep_pct = None
    if latest_sleep and latest_sleep.day == target_date and latest_sleep.total_sleep_sec and latest_sleep.deep_sleep_sec:
        deep_pct = latest_sleep.deep_sleep_sec / latest_sleep.total_sleep_sec

    vo2_now = latest(training, "vo2max")
    vo2_4w = None
    if len(training) >= 28:
        vo2_4w = next((t.vo2max for t in training[:-21] if t.vo2max is not None), None)

    # days since last run / consecutive active days (based on activity start dates)
    act_days = sorted({a.start_time.date() for a in acts if a.start_time}, reverse=True)
    run_days = [a.start_time.date() for a in acts if a.start_time and _is_run(a.activity_type)]
    days_since_last_run = (target_date - max(run_days)).days if run_days else None
    consecutive = 0
    cursor = target_date
    act_day_set = set(act_days)
    while cursor in act_day_set:
        consecutive += 1
        cursor = cursor - timedelta(days=1)
        
    has_target_data = False
    for s in (wellness, sleep, training):
        if s and hasattr(s[-1], 'day') and s[-1].day == target_date:
            has_target_data = True

    # --- i valori di «oggi», con la loro età ---------------------------------
    #
    # Non più «o è di oggi o non esiste»: ogni misura porta con sé quanto è
    # vecchia, e il motore di prontezza decide quanto farla contare. Prima, una
    # sincronizzazione in ritardo di un'ora faceva scomparire il punteggio
    # dalla schermata principale.
    sleep_score, sleep_age = latest_with_age(sleep, "sleep_score", target_date)
    battery, battery_age = latest_with_age(wellness, "body_battery_high", target_date)
    rhr_value, rhr_age = latest_with_age(wellness, "resting_hr", target_date)
    hrv_value, hrv_age = latest_with_age(training, "hrv_weekly_avg", target_date)
    hrv_status, hrv_status_age = latest_with_age(training, "hrv_status", target_date)

    checkin, checkin_age = _latest_checkin(db, user_id, target_date)

    return {
        "sleep_score": sleep_score,
        "sleep_score_7d_avg": _avg([s.sleep_score for s in sleep[-7:]]),
        "deep_pct": deep_pct,
        "hrv_status": hrv_status,
        "hrv_weekly_avg": hrv_value,
        "body_battery_high": battery,
        "body_battery_low_7d_avg": _avg([w.body_battery_low for w in wellness[-7:]]),
        "load_ratio": load.acwr if load else None,
        "ctl": load.ctl if load else None,
        "atl": load.atl if load else None,
        "tsb": load.tsb if load else None,
        "weekly_load": load.weekly_load if load else None,
        "previous_weekly_load": load.previous_weekly_load if load else None,
        "monotony": load.monotony if load else None,
        "load_is_measured": bool(load and load.is_reliable),
        "easy_time_pct": load.zones.easy_pct if (load and load.zones.is_readable) else None,
        "grey_time_pct": load.zones.grey_pct if (load and load.zones.is_readable) else None,
        "hard_time_pct": load.zones.hard_pct if (load and load.zones.is_readable) else None,
        "resting_hr_latest": rhr_value,
        "resting_hr_7d_avg": _avg(rhr_7),
        "resting_hr_30d_avg": _avg(rhr_all),
        "vo2max_latest": vo2_now,
        "vo2max_4w_ago": vo2_4w,
        "days_since_last_run": days_since_last_run,
        "consecutive_active_days": consecutive,
        "steps_latest": exact_latest(wellness, "total_steps", target_date),
        "step_goal": exact_latest(wellness, "step_goal", target_date),
        "avg_stress_latest": exact_latest(wellness, "avg_stress", target_date),
        "has_data": bool(wellness or sleep or training or checkin),
        "has_target_data": has_target_data,

        # --- il check-in del mattino ---------------------------------------
        "checkin": checkin,
        "checkin_age": checkin_age,

        # --- quanto sono vecchie le misure, in giorni -----------------------
        "ages": {
            "sleep": sleep_age,
            "battery": battery_age,
            "rhr": rhr_age,
            "hrv": hrv_age if hrv_value is not None else hrv_status_age,
            "subjective": checkin_age,
        },

        # --- il normale di questa persona, per leggere i valori di oggi -----
        #
        # Le distribuzioni viaggiano dentro lo snapshot invece di essere
        # ricalcolate dal motore di prontezza: le serie sono già state lette
        # qui, e rileggerle sarebbe pagare due volte le stesse query.
        "distributions": {
            "sleep": baseline.of([s.sleep_score for s in sleep]),
            "battery": baseline.of([w.body_battery_high for w in wellness]),
            "hrv": baseline.of([t.hrv_weekly_avg for t in training]),
            "rhr": baseline.of(
                [w.resting_hr for w in wellness], higher_is_better=False
            ),
        },
    }


def _latest_checkin(db: Session, user_id: int, target_date: date):
    """L'ultimo check-in non oltre la data, come dizionario, e la sua età."""
    row = db.scalar(
        select(DailyCheckin)
        .where(DailyCheckin.user_id == user_id, DailyCheckin.day <= target_date)
        .order_by(DailyCheckin.day.desc())
        .limit(1)
    )
    if row is None:
        return None, None
    return (
        {
            "energy": row.energy,
            "legs": row.legs,
            "mood": row.mood,
            "sleep_quality": row.sleep_quality,
            "note": row.note,
            "day": row.day,
        },
        (target_date - row.day).days,
    )
