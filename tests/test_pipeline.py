"""Pipeline giornaliera e scheduler."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.db.models import DailyCoachCache, DailyWellness, SleepRecord, TrainingMetric, User
from app.garmin.client import GarminClientError


@pytest.fixture()
def user(db) -> User:
    u = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add(u)
    db.commit()
    return u


@pytest.fixture()
def user_with_data(db, user) -> User:
    today = date.today()
    for i in range(30):
        db.add(DailyWellness(
            user_id=user.id, day=today - timedelta(days=i),
            total_steps=9000, resting_hr=52, body_battery_high=88, body_battery_low=35,
        ))
    db.add(SleepRecord(user_id=user.id, day=today, total_sleep_sec=7 * 3600,
                       deep_sleep_sec=5400, sleep_score=82))
    db.add(TrainingMetric(user_id=user.id, day=today, vo2max=52, training_load=250,
                          hrv_status="balanced"))
    db.commit()
    return user


# --------------------------- refresh cache ---------------------------

def test_refresh_daily_cache_creates_row(db, user_with_data):
    from app.pipeline import refresh_daily_cache

    row = refresh_daily_cache(db, user_with_data)
    assert row.readiness_score is not None and row.readiness_score > 0
    assert row.readiness_label
    assert row.source == "deterministic"
    assert isinstance(row.insights_json, list)


def test_refresh_daily_cache_is_idempotent(db, user_with_data):
    from app.pipeline import refresh_daily_cache

    first = refresh_daily_cache(db, user_with_data)
    second = refresh_daily_cache(db, user_with_data)
    assert first.id == second.id
    assert db.query(DailyCoachCache).count() == 1


def test_refresh_daily_cache_preserves_ai_message(db, user_with_data):
    """Un messaggio generato dall'AI non viene sovrascritto dal ricalcolo."""
    from app.pipeline import refresh_daily_cache

    row = refresh_daily_cache(db, user_with_data)
    row.coach_message = "Messaggio del coach AI"
    row.source = "ai"
    db.commit()

    again = refresh_daily_cache(db, user_with_data)
    assert again.coach_message == "Messaggio del coach AI"
    assert again.source == "ai"


def test_cache_is_per_user(db, user_with_data):
    from app.pipeline import refresh_daily_cache

    other = User(garmin_email="b@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add(other)
    db.commit()

    refresh_daily_cache(db, user_with_data)
    refresh_daily_cache(db, other)
    assert db.query(DailyCoachCache).count() == 2


# --------------------------- sync ---------------------------

def test_do_sync_records_success(db, user, monkeypatch):
    from app import pipeline

    monkeypatch.setattr(pipeline, "sync_all", lambda d, u: {"activities": 3})
    result = pipeline.PipelineResult(user_id=user.id)
    pipeline.do_sync(db, user, result)

    assert result.synced == {"activities": 3}
    assert result.errors == []
    assert user.last_sync_at is not None
    assert user.sync_failures == 0


def test_do_sync_counts_failures(db, user, monkeypatch):
    from app import pipeline

    def boom(d, u):
        raise GarminClientError("Garmin non raggiungibile")

    monkeypatch.setattr(pipeline, "sync_all", boom)
    result = pipeline.PipelineResult(user_id=user.id)

    pipeline.do_sync(db, user, result)
    pipeline.do_sync(db, user, result)

    assert user.sync_failures == 2
    assert len(result.errors) == 2
    assert "Garmin non raggiungibile" in result.errors[0]


def test_sync_failure_counter_resets_on_success(db, user, monkeypatch):
    from app import pipeline

    user.sync_failures = 5
    db.commit()
    monkeypatch.setattr(pipeline, "sync_all", lambda d, u: {"activities": 1})
    pipeline.do_sync(db, user, pipeline.PipelineResult(user_id=user.id))
    assert user.sync_failures == 0


# --------------------------- orchestrazione ---------------------------

def test_run_for_user_full_pipeline(db, user_with_data, monkeypatch):
    from app import pipeline

    monkeypatch.setattr(pipeline, "sync_all", lambda d, u: {"activities": 2, "sleep": 7})
    result = pipeline.run_for_user(db, user_with_data)

    assert result.ok
    assert result.synced == {"activities": 2, "sleep": 7}
    assert result.readiness is not None
    assert db.query(DailyCoachCache).count() == 1


def test_run_for_user_without_sync(db, user_with_data, monkeypatch):
    from app import pipeline

    def should_not_be_called(d, u):
        raise AssertionError("la sync non doveva partire")

    monkeypatch.setattr(pipeline, "sync_all", should_not_be_called)
    result = pipeline.run_for_user(db, user_with_data, with_sync=False)
    assert result.ok and result.synced is None and result.readiness is not None


def test_coaching_runs_even_if_sync_failed(db, user_with_data, monkeypatch):
    """Se Garmin è irraggiungibile si usano i dati già in cassa."""
    from app import pipeline

    def boom(d, u):
        raise GarminClientError("offline")

    monkeypatch.setattr(pipeline, "sync_all", boom)
    result = pipeline.run_for_user(db, user_with_data)

    assert not result.ok
    assert result.readiness is not None  # il coaching è comunque avvenuto


def test_run_for_all_skips_inactive_users(db, monkeypatch):
    from app import pipeline

    active = User(garmin_email="a@x.it", garmin_password_encrypted="e",
                  garmin_password_hash="h", is_active=True)
    inactive = User(garmin_email="b@x.it", garmin_password_encrypted="e",
                    garmin_password_hash="h", is_active=False)
    db.add_all([active, inactive])
    db.commit()

    monkeypatch.setattr(pipeline, "sync_all", lambda d, u: {})
    results = pipeline.run_for_all(db)
    assert [r.user_id for r in results] == [active.id]


def test_run_for_all_isolates_failures(db, monkeypatch):
    """Un utente che esplode non deve bloccare gli altri."""
    from app import pipeline

    u1 = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    u2 = User(garmin_email="b@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add_all([u1, u2])
    db.commit()

    def selective_boom(d, u):
        if u.id == u1.id:
            raise RuntimeError("crash")
        return {"activities": 1}

    monkeypatch.setattr(pipeline, "sync_all", selective_boom)
    results = pipeline.run_for_all(db)

    assert len(results) == 2
    by_user = {r.user_id: r for r in results}
    assert not by_user[u1.id].ok
    assert by_user[u2.id].ok


# --------------------------- scheduler ---------------------------

def test_scheduler_disabled_returns_none(monkeypatch):
    from app import scheduler

    monkeypatch.setattr("app.config.settings.SCHEDULER_ENABLED", False)
    scheduler._scheduler = None
    assert scheduler.start_scheduler() is None


def test_scheduler_registers_daily_job(monkeypatch):
    from app import scheduler

    monkeypatch.setattr("app.config.settings.SCHEDULER_ENABLED", True)
    monkeypatch.setattr("app.config.settings.SYNC_HOUR", 6)
    monkeypatch.setattr("app.config.settings.SYNC_MINUTE", 30)
    scheduler._scheduler = None
    try:
        sched = scheduler.start_scheduler()
        assert sched is not None
        job = sched.get_job("daily_sync")
        assert job is not None
        assert scheduler.next_run_time() is not None
        assert job.next_run_time.hour == 6 and job.next_run_time.minute == 30
    finally:
        scheduler.stop_scheduler()


def test_daily_job_processes_every_user(db, monkeypatch, test_db):
    from app import scheduler

    u1 = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    u2 = User(garmin_email="b@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add_all([u1, u2])
    db.commit()

    processed = []
    monkeypatch.setattr(scheduler, "SessionLocal", test_db)
    monkeypatch.setattr(scheduler, "run_for_user", lambda d, u: processed.append(u.id))
    monkeypatch.setattr("app.config.settings.SYNC_STAGGER_MINUTES", 0)

    scheduler.run_daily_job()
    assert processed == [u1.id, u2.id]


# ============================================================================
# Regressioni emerse solo in produzione (Postgres)
# ============================================================================

def test_activity_id_column_is_64_bit():
    """Gli id di Garmin hanno superato i 2^31.

    Su SQLite gli interi sono a 64 bit e il problema non si vede; su Postgres
    `INTEGER` sta in 4 byte e la query esplode con «integer out of range».
    """
    from sqlalchemy import BigInteger

    from app.db.models import Activity

    column = Activity.__table__.c.garmin_activity_id
    assert isinstance(column.type, BigInteger), (
        "garmin_activity_id deve essere BigInteger: gli id Garmin superano i 2,1 miliardi"
    )


def test_a_real_garmin_id_fits(db):
    """Un id realistico (oltre 2^31) deve poter essere salvato e riletto."""
    from app.db.models import Activity, User

    user = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add(user)
    db.commit()

    big_id = 21_474_836_470  # dieci volte il limite di INTEGER
    db.add(Activity(user_id=user.id, garmin_activity_id=big_id, activity_type="running"))
    db.commit()

    from app import queries as q

    assert q.get_activity(db, user.id, big_id) is not None


def test_sync_failure_rolls_back_before_writing(db, user, monkeypatch):
    """Se l'errore veniva dal database, senza rollback il commit successivo
    fallisce a sua volta: su Postgres la transazione resta avvelenata."""
    from app import pipeline

    rolled_back = []
    original_rollback = db.rollback

    def tracking_rollback():
        rolled_back.append(True)
        original_rollback()

    monkeypatch.setattr(db, "rollback", tracking_rollback)

    def database_error(d, u):
        raise RuntimeError("current transaction is aborted")

    monkeypatch.setattr(pipeline, "sync_all", database_error)

    result = pipeline.PipelineResult(user_id=user.id)
    pipeline.do_sync(db, user, result)

    assert rolled_back, "il rollback deve precedere la scrittura del contatore"
    assert user.sync_failures == 1
    assert len(result.errors) == 1


def test_failure_counter_survives_a_broken_session(db, user, monkeypatch):
    """Anche se registrare il fallimento non riesce, la pipeline non esplode."""
    from app import pipeline

    monkeypatch.setattr(pipeline, "sync_all",
                        lambda d, u: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(db, "commit",
                        lambda: (_ for _ in ()).throw(RuntimeError("sessione rotta")))

    result = pipeline.PipelineResult(user_id=user.id)
    pipeline.do_sync(db, user, result)  # non solleva
    assert result.errors
