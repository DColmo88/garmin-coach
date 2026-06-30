"""Query di lettura dal DB: serie storiche ordinate per i grafici e le tabelle."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Activity,
    BodyComposition,
    DailyWellness,
    SleepRecord,
    TrainingMetric,
)


def _asc(rows: list) -> list:
    """Ordina per giorno crescente (per i grafici temporali)."""
    return list(reversed(rows))


def recent_activities(db: Session, limit: int = 50) -> list[Activity]:
    return list(
        db.scalars(select(Activity).order_by(Activity.start_time.desc()).limit(limit)).all()
    )


def get_activity(db: Session, activity_id: int) -> Activity | None:
    return db.scalar(select(Activity).where(Activity.garmin_activity_id == activity_id))


def wellness_series(db: Session, days: int = 28) -> list[DailyWellness]:
    rows = db.scalars(
        select(DailyWellness).order_by(DailyWellness.day.desc()).limit(days)
    ).all()
    return _asc(list(rows))


def sleep_series(db: Session, days: int = 28) -> list[SleepRecord]:
    rows = db.scalars(
        select(SleepRecord).order_by(SleepRecord.day.desc()).limit(days)
    ).all()
    return _asc(list(rows))


def training_series(db: Session, days: int = 28) -> list[TrainingMetric]:
    rows = db.scalars(
        select(TrainingMetric).order_by(TrainingMetric.day.desc()).limit(days)
    ).all()
    return _asc(list(rows))


def body_series(db: Session, days: int = 90) -> list[BodyComposition]:
    rows = db.scalars(
        select(BodyComposition).order_by(BodyComposition.day.desc()).limit(days)
    ).all()
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


def coach_snapshot(db: Session) -> dict:
    """Costruisce lo snapshot deterministico per readiness/insights/coaching.

    Tutto None-safe: campi mancanti -> None, mai eccezioni.
    """
    wellness = wellness_series(db, 30)          # crescente
    sleep = sleep_series(db, 30)
    training = training_series(db, 30)
    acts = recent_activities(db, 50)            # desc per start_time

    rhr_all = [w.resting_hr for w in wellness]
    rhr_7 = [w.resting_hr for w in wellness[-7:]]
    load_7 = [t.training_load for t in training[-7:]]
    load_28 = [t.training_load for t in training[-28:]]
    avg_load_7 = _avg(load_7)
    avg_load_28 = _avg(load_28)
    load_ratio = (avg_load_7 / avg_load_28) if (avg_load_7 is not None and avg_load_28) else None

    latest_sleep = sleep[-1] if sleep else None
    deep_pct = None
    if latest_sleep and latest_sleep.total_sleep_sec and latest_sleep.deep_sleep_sec:
        deep_pct = latest_sleep.deep_sleep_sec / latest_sleep.total_sleep_sec

    vo2_now = latest(training, "vo2max")
    vo2_4w = None
    if len(training) >= 28:
        vo2_4w = next((t.vo2max for t in training[:-21] if t.vo2max is not None), None)

    # days since last run / consecutive active days (based on activity start dates)
    act_days = sorted({a.start_time.date() for a in acts if a.start_time}, reverse=True)
    run_days = [a.start_time.date() for a in acts if a.start_time and _is_run(a.activity_type)]
    today = date.today()
    days_since_last_run = (today - max(run_days)).days if run_days else None
    consecutive = 0
    cursor = today
    act_day_set = set(act_days)
    while cursor in act_day_set:
        consecutive += 1
        cursor = cursor - timedelta(days=1)

    return {
        "sleep_score": latest(sleep, "sleep_score"),
        "sleep_score_7d_avg": _avg([s.sleep_score for s in sleep[-7:]]),
        "deep_pct": deep_pct,
        "hrv_status": latest(training, "hrv_status"),
        "hrv_weekly_avg": latest(training, "hrv_weekly_avg"),
        "body_battery_high": latest(wellness, "body_battery_high"),
        "body_battery_low_7d_avg": _avg([w.body_battery_low for w in wellness[-7:]]),
        "load_ratio": load_ratio,
        "resting_hr_latest": latest(wellness, "resting_hr"),
        "resting_hr_7d_avg": _avg(rhr_7),
        "resting_hr_30d_avg": _avg(rhr_all),
        "vo2max_latest": vo2_now,
        "vo2max_4w_ago": vo2_4w,
        "days_since_last_run": days_since_last_run,
        "consecutive_active_days": consecutive,
        "steps_latest": latest(wellness, "total_steps"),
        "step_goal": latest(wellness, "step_goal"),
        "avg_stress_latest": latest(wellness, "avg_stress"),
        "has_data": bool(wellness or sleep or training),
    }
