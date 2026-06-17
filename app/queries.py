"""Query di lettura dal DB: serie storiche ordinate per i grafici e le tabelle."""
from __future__ import annotations

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
