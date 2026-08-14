"""L'obiettivo attivo deve influenzare il coaching, non restare un dato inerte."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import goals
from app.ai.coaching import build_coach_output
from app.ai.readiness import compute_readiness
from app.db.models import User

SNAP_GOOD = {
    "sleep_score": 85, "hrv_status": "balanced", "body_battery_high": 92,
    "load_ratio": 1.0, "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 52,
}
SNAP_TIRED = {
    "sleep_score": 40, "hrv_status": "unbalanced", "body_battery_high": 25,
    "load_ratio": 1.6, "resting_hr_7d_avg": 60, "resting_hr_30d_avg": 55,
}


@pytest.fixture()
def user(db) -> User:
    u = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add(u)
    db.commit()
    return u


def test_message_without_goal_has_no_goal_line(db, user):
    out = build_coach_output(SNAP_GOOD, compute_readiness(SNAP_GOOD), None)
    assert "Obiettivo" not in out.message
    assert "«" not in out.message


def test_message_mentions_goal_without_date(db, user):
    goal = goals.set_active_goal(db, user, "fitness")
    out = build_coach_output(SNAP_GOOD, compute_readiness(SNAP_GOOD), goal)
    assert "Forma generale" in out.message


def test_taper_advice_in_the_final_week(db, user):
    goal = goals.set_active_goal(
        db, user, "race_10k", target_date=date.today() + timedelta(days=5)
    )
    out = build_coach_output(SNAP_GOOD, compute_readiness(SNAP_GOOD), goal)
    assert "scarico" in out.message.lower()


def test_race_day_message(db, user):
    goal = goals.set_active_goal(db, user, "race_10k", target_date=date.today())
    out = build_coach_output(SNAP_GOOD, compute_readiness(SNAP_GOOD), goal)
    assert "Oggi è il giorno" in out.message


def test_low_readiness_near_race_advises_recovery(db, user):
    goal = goals.set_active_goal(
        db, user, "race_marathon", target_date=date.today() + timedelta(days=18)
    )
    out = build_coach_output(SNAP_TIRED, compute_readiness(SNAP_TIRED), goal)
    assert "recuperare" in out.message.lower()


def test_past_target_date_prompts_update(db, user):
    goal = goals.set_active_goal(
        db, user, "race_5k", target_date=date.today() - timedelta(days=3)
    )
    out = build_coach_output(SNAP_GOOD, compute_readiness(SNAP_GOOD), goal)
    assert "aggiorna l'obiettivo" in out.message.lower()


def test_no_data_message_ignores_goal(db, user):
    """Senza dati il messaggio resta l'invito a sincronizzare."""
    goal = goals.set_active_goal(db, user, "race_10k", target_date=date.today())
    empty = {"sleep_score": None, "hrv_status": None, "body_battery_high": None,
             "load_ratio": None, "resting_hr_7d_avg": None, "resting_hr_30d_avg": None}
    out = build_coach_output(empty, compute_readiness(empty), goal)
    assert "Sincronizza" in out.message


def test_pipeline_stores_goal_aware_message(db, user, monkeypatch):
    """Il messaggio salvato in cache dalla pipeline contiene l'obiettivo."""
    from app.db.models import DailyWellness, SleepRecord, TrainingMetric
    from app.pipeline import refresh_daily_cache

    today = date.today()
    db.add(DailyWellness(user_id=user.id, day=today, resting_hr=52, body_battery_high=90))
    db.add(SleepRecord(user_id=user.id, day=today, sleep_score=85, total_sleep_sec=27000))
    db.add(TrainingMetric(user_id=user.id, day=today, hrv_status="balanced"))
    db.commit()

    goals.set_active_goal(db, user, "race_half", target_date=today + timedelta(days=4))
    row = refresh_daily_cache(db, user)

    assert "scarico" in (row.coach_message or "").lower()
    assert row.workout_json and row.workout_json["type"]


def test_coach_page_shows_goal_card(logged_client):
    logged_client.post("/goals", data={"goal_type": "race_10k"})
    page = logged_client.get("/coach").text
    assert "coach-goal" in page and "Gara 10K" in page


def test_coach_page_invites_to_set_a_goal_when_none(logged_client):
    page = logged_client.get("/coach").text
    assert "Impostane uno" in page
