"""Lo snapshot deterministico che alimenta readiness, insights e coaching."""
from datetime import date, timedelta

import pytest

from app.db.models import DailyWellness, SleepRecord, TrainingMetric, User
from app.queries import coach_snapshot


@pytest.fixture()
def user(db) -> User:
    u = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add(u)
    db.commit()
    return u


def test_snapshot_empty_db_is_safe(db, user):
    snap = coach_snapshot(db, user.id)
    assert snap["has_data"] is False
    assert snap["sleep_score"] is None
    assert snap["consecutive_active_days"] == 0
    assert snap["days_since_last_run"] is None


def test_snapshot_computes_latest_and_trends(db, user):
    today = date(2026, 7, 1)
    # 30 giorni di wellness: FC a riposo in calo (i giorni più vecchi più alti)
    for i in range(30):
        db.add(DailyWellness(
            user_id=user.id,
            day=today - timedelta(days=i),
            total_steps=8000, step_goal=10000,
            resting_hr=60 if i < 7 else 64,
            body_battery_high=90, body_battery_low=20,
            avg_stress=35,
        ))
    db.add(SleepRecord(user_id=user.id, day=today, total_sleep_sec=7 * 3600,
                       deep_sleep_sec=3600, sleep_score=78))
    db.add(TrainingMetric(user_id=user.id, day=today, vo2max=50, training_load=200,
                          hrv_weekly_avg=65, hrv_status="balanced"))
    db.commit()

    snap = coach_snapshot(db, user.id)
    assert snap["has_data"] is True
    assert snap["sleep_score"] == 78
    assert snap["resting_hr_latest"] == 60
    assert snap["resting_hr_7d_avg"] == 60           # gli ultimi 7 giorni sono tutti 60
    assert round(snap["deep_pct"], 2) == 0.14        # 3600 / 25200
    assert snap["hrv_status"] == "balanced"
    assert snap["step_goal"] == 10000
