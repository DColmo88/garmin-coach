from datetime import date, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import Activity, DailyWellness, SleepRecord, TrainingMetric
from app.queries import coach_snapshot


def _session() -> Session:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return Session(engine)


def test_snapshot_empty_db_is_safe():
    db = _session()
    snap = coach_snapshot(db)
    assert snap["has_data"] is False
    assert snap["sleep_score"] is None
    assert snap["consecutive_active_days"] == 0
    assert snap["days_since_last_run"] is None


def test_snapshot_computes_latest_and_trends():
    db = _session()
    today = date(2026, 7, 1)
    # 30 days of wellness: resting_hr trending down (older higher)
    for i in range(30):
        db.add(DailyWellness(
            day=today - timedelta(days=i),
            total_steps=8000, step_goal=10000,
            resting_hr=60 if i < 7 else 64,
            body_battery_high=90, body_battery_low=20,
            avg_stress=35,
        ))
    db.add(SleepRecord(day=today, total_sleep_sec=7*3600,
                       deep_sleep_sec=3600, sleep_score=78))
    db.add(TrainingMetric(day=today, vo2max=50, training_load=200,
                          hrv_weekly_avg=65, hrv_status="balanced"))
    db.commit()

    snap = coach_snapshot(db)
    assert snap["has_data"] is True
    assert snap["sleep_score"] == 78
    assert snap["resting_hr_latest"] == 60
    assert snap["resting_hr_7d_avg"] == 60          # last 7 days all 60
    assert round(snap["deep_pct"], 2) == 0.14        # 3600 / 25200
    assert snap["hrv_status"] == "balanced"
    assert snap["step_goal"] == 10000
