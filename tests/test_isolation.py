"""Isolamento dei dati fra utenti.

È il test più importante del multi-utente: se una query dimentica il filtro
`user_id`, un utente vede i dati di un altro. Ogni funzione di lettura viene
verificata con due utenti che hanno dati nello stesso giorno.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app import queries as q
from app.db.models import (
    Activity,
    BodyComposition,
    DailyWellness,
    SleepRecord,
    TrainingMetric,
    User,
)

DAY = date(2026, 8, 1)


@pytest.fixture()
def two_users(db):
    alice = User(garmin_email="alice@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    bob = User(garmin_email="bob@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add_all([alice, bob])
    db.flush()

    db.add_all([
        SleepRecord(user_id=alice.id, day=DAY, sleep_score=90, total_sleep_sec=28800,
                    deep_sleep_sec=7200),
        SleepRecord(user_id=bob.id, day=DAY, sleep_score=30, total_sleep_sec=14400,
                    deep_sleep_sec=1200),
        DailyWellness(user_id=alice.id, day=DAY, total_steps=15000, resting_hr=48,
                      body_battery_high=95),
        DailyWellness(user_id=bob.id, day=DAY, total_steps=2000, resting_hr=72,
                      body_battery_high=40),
        TrainingMetric(user_id=alice.id, day=DAY, vo2max=58, training_load=300,
                       hrv_status="balanced"),
        TrainingMetric(user_id=bob.id, day=DAY, vo2max=35, training_load=80,
                       hrv_status="unbalanced"),
        BodyComposition(user_id=alice.id, day=DAY, weight_g=70000),
        BodyComposition(user_id=bob.id, day=DAY, weight_g=95000),
        Activity(user_id=alice.id, garmin_activity_id=1, name="Corsa di Alice",
                 activity_type="running", start_time=datetime(2026, 8, 1, 7, 0),
                 distance_m=10000),
        Activity(user_id=bob.id, garmin_activity_id=2, name="Corsa di Bob",
                 activity_type="running", start_time=datetime(2026, 8, 1, 18, 0),
                 distance_m=3000),
    ])
    db.commit()
    return alice, bob


def test_sleep_series_isolated(db, two_users):
    alice, bob = two_users
    assert [r.sleep_score for r in q.sleep_series(db, alice.id, 28)] == [90]
    assert [r.sleep_score for r in q.sleep_series(db, bob.id, 28)] == [30]


def test_wellness_series_isolated(db, two_users):
    alice, bob = two_users
    assert [r.total_steps for r in q.wellness_series(db, alice.id, 28)] == [15000]
    assert [r.total_steps for r in q.wellness_series(db, bob.id, 28)] == [2000]


def test_training_series_isolated(db, two_users):
    alice, bob = two_users
    assert [r.vo2max for r in q.training_series(db, alice.id, 28)] == [58]
    assert [r.vo2max for r in q.training_series(db, bob.id, 28)] == [35]


def test_body_series_isolated(db, two_users):
    alice, bob = two_users
    assert [r.weight_g for r in q.body_series(db, alice.id, 90)] == [70000]
    assert [r.weight_g for r in q.body_series(db, bob.id, 90)] == [95000]


def test_recent_activities_isolated(db, two_users):
    alice, bob = two_users
    assert [a.name for a in q.recent_activities(db, alice.id)] == ["Corsa di Alice"]
    assert [a.name for a in q.recent_activities(db, bob.id)] == ["Corsa di Bob"]


def test_get_activity_does_not_leak_across_users(db, two_users):
    alice, bob = two_users
    # L'attività 2 è di Bob: Alice non deve poterla aprire dal suo URL.
    assert q.get_activity(db, bob.id, 2) is not None
    assert q.get_activity(db, alice.id, 2) is None


def test_coach_snapshot_isolated(db, two_users):
    alice, bob = two_users
    snap_a = q.coach_snapshot(db, alice.id)
    snap_b = q.coach_snapshot(db, bob.id)
    assert snap_a["sleep_score"] == 90 and snap_b["sleep_score"] == 30
    assert snap_a["vo2max_latest"] == 58 and snap_b["vo2max_latest"] == 35
    assert snap_a["hrv_status"] == "balanced" and snap_b["hrv_status"] == "unbalanced"


def test_ai_context_isolated(db, two_users):
    from app.ai.context import build_ai_context

    alice, bob = two_users
    ctx_a = build_ai_context(db, alice.id)
    assert [a["name"] for a in ctx_a["activities"]] == ["Corsa di Alice"]
    assert [s["sleep_score"] for s in ctx_a["sleep"]] == [90]


def test_upsert_is_scoped_to_user(db, two_users):
    """Due utenti possono avere lo stesso giorno senza collidere."""
    from app.garmin.sync import _upsert

    alice, bob = two_users
    new_day = DAY + timedelta(days=1)

    row_a, new_a = _upsert(db, SleepRecord, alice.id, new_day)
    db.add(row_a)
    db.commit()
    row_b, new_b = _upsert(db, SleepRecord, bob.id, new_day)
    db.add(row_b)
    db.commit()

    assert new_a and new_b and row_a.id != row_b.id

    # Ri-upsert dello stesso giorno: aggiorna, non duplica.
    again, is_new = _upsert(db, SleepRecord, alice.id, new_day)
    assert not is_new and again.id == row_a.id


def test_live_service_cache_is_keyed_by_user(db, two_users):
    """La cache in memoria non deve restituire a Bob i dati di Alice."""
    from app.garmin import service

    alice, bob = two_users
    service.clear_cache()

    assert service._cached(alice.id, "profilo", lambda: "dati-di-alice") == "dati-di-alice"
    assert service._cached(bob.id, "profilo", lambda: "dati-di-bob") == "dati-di-bob"
    # rileggendo, ognuno ritrova i propri
    assert service._cached(alice.id, "profilo", lambda: "MAI") == "dati-di-alice"

    service.clear_cache(alice.id)
    assert service._cached(bob.id, "profilo", lambda: "MAI") == "dati-di-bob"


# --------------------------- protezione del DB reale ---------------------------

def test_session_factory_points_at_the_test_database(test_db):
    """Guardia: chi apre una sessione per conto suo deve finire sul DB di test.

    Lo scheditore e lo streaming SSE non passano dalla dependency di FastAPI.
    Se questo test fallisce, una suite può scrivere sul database vero.
    """
    import app.db.database as database
    import app.routers.chat as chat_router
    import app.scheduler as scheduler

    real_url = "data/garmin_connector.db"
    for module, name in (
        (database, "SessionLocal"),
        (chat_router, "SessionLocal"),
        (scheduler, "SessionLocal"),
    ):
        factory = getattr(module, name)
        url = str(factory.kw["bind"].url)
        assert real_url not in url, f"{module.__name__}.{name} punta al DB reale: {url}"
