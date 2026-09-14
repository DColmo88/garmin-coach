"""Il quadro del carico, e il fatto che arrivi fino alla prontezza.

Il bug che questi test presidiano: Garmin, per molti account, non popola mai
`training_load`. Il rapporto di carico restava `None`, il fattore «Carico»
restituiva sempre 65, e il 15% del punteggio di prontezza era una costante
travestita da misura.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.analysis import summary
from app.db.models import Activity, DailyWellness, SleepRecord, TrainingMetric, User


@pytest.fixture()
def user(db) -> User:
    u = User(email="a@x.it", password_hash="h")
    db.add(u)
    db.commit()
    return u


def _add_activity(db, user, days_ago: int, minutes: int = 60,
                  avg_hr: float | None = 150, n: int = 0, **kw):
    db.add(Activity(
        user_id=user.id,
        external_id=days_ago * 100 + n,
        activity_type=kw.pop("activity_type", "running"),
        start_time=datetime.combine(date.today() - timedelta(days=days_ago),
                                    datetime.min.time()),
        duration_sec=minutes * 60,
        avg_hr=avg_hr,
        max_hr=kw.pop("max_hr", 185),
        distance_m=kw.pop("distance_m", 10000),
        **kw,
    ))


def test_the_load_is_computed_even_when_garmin_sends_none(db, user):
    """Il caso reale: 28 giorni di metriche, zero valori di training_load."""
    for i in range(28):
        db.add(TrainingMetric(user_id=user.id, day=date.today() - timedelta(days=i),
                              vo2max=52, training_load=None))
    for i in range(0, 28, 2):
        _add_activity(db, user, i)
    db.commit()

    load = summary.build(db, user)

    assert load.has_data
    assert load.ctl is not None and load.ctl > 0
    assert load.acwr is not None


def test_without_activities_nothing_is_invented(db, user):
    load = summary.build(db, user)

    assert not load.has_data
    assert load.acwr is None
    assert load.weekly_load == 0


def test_the_source_of_the_load_is_reported(db, user):
    _add_activity(db, user, 1, avg_hr=150)
    _add_activity(db, user, 2, avg_hr=None, n=1)  # solo durata
    db.commit()

    load = summary.build(db, user)

    assert load.main_source == "frequenza cardiaca"
    assert load.measured_fraction == pytest.approx(0.5)
    assert not load.is_reliable  # metà dei carichi è una stima grezza


def test_a_history_made_of_real_measurements_is_reliable(db, user):
    for i in range(10):
        _add_activity(db, user, i, avg_hr=145)
    db.commit()

    assert summary.build(db, user).is_reliable


def test_old_activities_stay_out_of_the_window(db, user):
    _add_activity(db, user, 400)
    db.commit()

    assert not summary.build(db, user).has_data


def test_the_chart_returns_aligned_series(db, user):
    for i in range(20):
        _add_activity(db, user, i)
    db.commit()

    chart = summary.build(db, user).chart(days=30)

    assert len(chart["labels"]) == 30
    assert len({len(v) for v in chart.values()}) == 1


# ============================================================================
# Il collegamento con la prontezza
# ============================================================================

def _seed_wellness(db, user, days: int = 30):
    for i in range(days):
        day = date.today() - timedelta(days=i)
        db.add(DailyWellness(user_id=user.id, day=day, resting_hr=52,
                             body_battery_high=85))
        db.add(SleepRecord(user_id=user.id, day=day, sleep_score=80))


def test_the_snapshot_carries_the_computed_load(db, user):
    from app import queries as q

    _seed_wellness(db, user)
    for i in range(0, 28, 2):
        _add_activity(db, user, i)
    db.commit()

    snap = q.coach_snapshot(db, user.id)

    assert snap["load_ratio"] is not None
    assert snap["ctl"] is not None and snap["tsb"] is not None
    assert snap["load_is_measured"] is True


def test_a_hard_week_moves_the_readiness_load_factor(db, user):
    """Il test che descrive il bug: prima questo fattore valeva sempre 65."""
    from app import queries as q
    from app.ai.readiness import compute_readiness

    _seed_wellness(db, user)
    # Tre settimane leggere, poi una settimana molto dura.
    for i in range(7, 28, 3):
        _add_activity(db, user, i, minutes=30, avg_hr=125)
    for i in range(7):
        _add_activity(db, user, i, minutes=90, avg_hr=165, n=5)
    db.commit()

    readiness = compute_readiness(q.coach_snapshot(db, user.id))
    load_factor = next(f for f in readiness.breakdown if f.name == "Carico")

    assert load_factor.value != 65
    assert load_factor.color in {"amber", "red"}


def test_a_steady_history_keeps_the_load_factor_healthy(db, user):
    """Tre sedute a settimana, sempre le stesse: nessuna salita da segnalare.

    Il ritmo è a cadenza **settimanale** e non «un giorno sì e uno no». Con
    quest'ultimo cadono quattro sedute nell'ultima settimana e 3,3 a settimana
    nelle tre precedenti: un aumento del 20% vero, che da quando le finestre
    del rapporto acuto/cronico non si sovrappongono viene visto per quello che
    è. Lo storico che il test vuole descrivere è quello di chi si allena
    sempre uguale, e per esserlo deve ripetersi ogni sette giorni.
    """
    from app import queries as q
    from app.ai.readiness import compute_readiness

    _seed_wellness(db, user)
    for i in range(28):
        if i % 7 in (0, 2, 4):
            _add_activity(db, user, i, minutes=50, avg_hr=140)
    db.commit()

    readiness = compute_readiness(q.coach_snapshot(db, user.id))
    load_factor = next(f for f in readiness.breakdown if f.name == "Carico")

    assert load_factor.color == "green"


def test_the_snapshot_survives_a_user_without_data(db, user):
    from app import queries as q

    snap = q.coach_snapshot(db, user.id)

    assert snap["load_ratio"] is None
    assert snap["load_is_measured"] is False
