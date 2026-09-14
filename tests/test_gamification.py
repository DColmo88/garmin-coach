"""XP, livelli, serie e traguardi: tutto ricalcolato dai dati."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app import gamification
from app.db.models import (
    Activity,
    DailyWellness,
    GamificationState,
    SleepRecord,
    TrainingMetric,
    User,
)


@pytest.fixture()
def user(db) -> User:
    from app.db.models import ProviderConnection

    u = User(email="a@x.it", password_hash="h")
    db.add(u)
    db.flush()
    db.add(ProviderConnection(user_id=u.id, provider="garmin",
                              external_id="a@x.it", secret_encrypted="cifrato"))
    db.commit()
    return u


def add_run(db, user_id: int, days_ago: int, km: float = 8, hour: int = 18, gid: int | None = None):
    when = datetime.combine(date.today() - timedelta(days=days_ago),
                            datetime.min.time()).replace(hour=hour)
    db.add(Activity(
        user_id=user_id,
        external_id=gid if gid is not None else days_ago * 1000 + hour,
        activity_type="running", start_time=when,
        distance_m=km * 1000, duration_sec=km * 360,
    ))


# ============================================================================
# Livelli
# ============================================================================

def test_levels_are_ordered_and_start_at_zero():
    thresholds = [xp for _, xp, _ in gamification.LEVELS]
    assert thresholds == sorted(thresholds)
    assert thresholds[0] == 0


@pytest.mark.parametrize("xp,expected", [
    (0, "Principiante"),
    (999, "Principiante"),
    (1_000, "Atleta"),
    (5_000, "Costante"),
    (15_000, "Elite"),
    (40_000, "Leggenda"),
    (999_999, "Leggenda"),
])
def test_level_for_xp(xp, expected):
    assert gamification._level_for(xp)[0] == expected


def test_progress_towards_the_next_level():
    _, _, next_level, to_next, pct = gamification._level_for(3_000)
    assert next_level == "Costante"
    assert to_next == 2_000
    assert pct == 50  # 2000 su 4000 fra Atleta e Costante


def test_top_level_has_no_next():
    _, _, next_level, to_next, pct = gamification._level_for(50_000)
    assert next_level is None and to_next == 0 and pct == 100


# ============================================================================
# Serie
# ============================================================================

def test_streak_counts_consecutive_days():
    today = date(2026, 8, 14)
    days = {today - timedelta(days=i) for i in range(5)}
    current, longest = gamification._streaks(days, today)
    assert current == 5 and longest == 5


def test_streak_survives_a_day_without_training_yet():
    """La serie non deve crollare ogni mattina prima dell'allenamento."""
    today = date(2026, 8, 14)
    days = {today - timedelta(days=i) for i in range(1, 6)}  # da ieri indietro
    current, _ = gamification._streaks(days, today)
    assert current == 5


def test_streak_breaks_after_two_missed_days():
    today = date(2026, 8, 14)
    days = {today - timedelta(days=i) for i in range(2, 7)}
    current, longest = gamification._streaks(days, today)
    assert current == 0 and longest == 5


def test_longest_streak_is_found_in_the_past():
    today = date(2026, 8, 14)
    days = {today - timedelta(days=i) for i in range(30, 40)}  # 10 giorni di fila
    current, longest = gamification._streaks(days, today)
    assert current == 0 and longest == 10


def test_no_activity_means_no_streak():
    assert gamification._streaks(set(), date.today()) == (0, 0)


# ============================================================================
# XP
# ============================================================================

def test_xp_from_activities(db, user):
    add_run(db, user.id, days_ago=1, km=10)
    db.commit()
    state = gamification.recompute(db, user)
    # 50 per l'attività + 1 per km
    assert state.total_xp == gamification.XP_PER_ACTIVITY + 10


def test_xp_from_good_sleep(db, user):
    db.add(SleepRecord(user_id=user.id, day=date.today(), sleep_score=85))
    db.add(SleepRecord(user_id=user.id, day=date.today() - timedelta(days=1), sleep_score=60))
    db.commit()
    state = gamification.recompute(db, user)
    assert state.total_xp == gamification.XP_GOOD_SLEEP  # solo la notte buona


def test_xp_from_step_goal(db, user):
    db.add(DailyWellness(user_id=user.id, day=date.today(),
                         total_steps=12000, step_goal=10000))
    db.add(DailyWellness(user_id=user.id, day=date.today() - timedelta(days=1),
                         total_steps=4000, step_goal=10000))
    db.commit()
    state = gamification.recompute(db, user)
    assert state.total_xp == gamification.XP_STEP_GOAL


def test_bonus_for_an_active_week(db, user):
    """Cinque giorni attivi **dentro la stessa settimana ISO**.

    «Gli ultimi cinque giorni» non bastava, e per undici mesi ha funzionato per
    caso: il bonus si conta per settimana di calendario, quindi cinque giorni a
    ritroso cascano tutti nella stessa settimana solo se oggi è venerdì, sabato
    o domenica. Di lunedì ne cadono quattro nella settimana prima e uno in
    quella corrente, e il test falliva senza che nulla fosse cambiato nel
    codice. Qui si parte dall'ultimo lunedì e si contano cinque giorni in
    avanti, così la settimana è una sola qualunque giorno sia oggi.
    """
    oggi = date.today()
    ultimo_lunedi = oggi - timedelta(days=oggi.weekday() + 7)
    for i in range(5):
        add_run(db, user.id, days_ago=(oggi - (ultimo_lunedi + timedelta(days=i))).days, km=5)
    db.commit()

    state = gamification.recompute(db, user)

    base = 5 * (gamification.XP_PER_ACTIVITY + 5)
    assert state.total_xp >= base + gamification.XP_ACTIVE_WEEK


def test_no_data_means_no_xp(db, user):
    state = gamification.recompute(db, user)
    assert state.total_xp == 0
    assert state.current_level == "Principiante"


# ============================================================================
# Traguardi
# ============================================================================

def test_first_10k_badge(db, user):
    add_run(db, user.id, days_ago=1, km=10.5)
    db.commit()
    assert "first_10k" in gamification.recompute(db, user).badges_json


def test_short_runs_do_not_earn_the_10k_badge(db, user):
    add_run(db, user.id, days_ago=1, km=6)
    db.commit()
    assert "first_10k" not in gamification.recompute(db, user).badges_json


def test_half_distance_badge(db, user):
    add_run(db, user.id, days_ago=3, km=21.1)
    db.commit()
    badges = gamification.recompute(db, user).badges_json
    assert "half" in badges and "first_10k" in badges


def test_streak_badges(db, user):
    for i in range(8):
        add_run(db, user.id, days_ago=i, km=4)
    db.commit()
    badges = gamification.recompute(db, user).badges_json
    assert "streak_7" in badges and "streak_30" not in badges


def test_sleep_week_badge(db, user):
    for i in range(7):
        db.add(SleepRecord(user_id=user.id, day=date.today() - timedelta(days=i),
                           sleep_score=85))
    db.commit()
    assert "sleep_week" in gamification.recompute(db, user).badges_json


def test_sleep_week_badge_needs_consecutive_nights(db, user):
    for i in (0, 1, 2, 4, 5, 6, 7):  # manca il terzo
        db.add(SleepRecord(user_id=user.id, day=date.today() - timedelta(days=i),
                           sleep_score=85))
    db.commit()
    assert "sleep_week" not in gamification.recompute(db, user).badges_json


def test_vo2max_badge(db, user):
    db.add(TrainingMetric(user_id=user.id, day=date.today() - timedelta(days=30), vo2max=48))
    db.add(TrainingMetric(user_id=user.id, day=date.today(), vo2max=50.5))
    db.commit()
    assert "vo2_up" in gamification.recompute(db, user).badges_json


def test_early_bird_badge(db, user):
    for i in range(10):
        add_run(db, user.id, days_ago=i, km=5, hour=6)
    db.commit()
    assert "early_bird" in gamification.recompute(db, user).badges_json


def test_early_bird_needs_ten_early_runs(db, user):
    for i in range(9):
        add_run(db, user.id, days_ago=i, km=5, hour=6)
    db.commit()
    assert "early_bird" not in gamification.recompute(db, user).badges_json


def test_century_badge(db, user):
    """100 km in un mese solare."""
    today = date.today().replace(day=1) + timedelta(days=5)
    for i in range(10):
        when = datetime.combine(today + timedelta(days=i), datetime.min.time())
        db.add(Activity(user_id=user.id, external_id=500 + i,
                        activity_type="running", start_time=when, distance_m=11000))
    db.commit()
    assert "century" in gamification.recompute(db, user).badges_json


def test_recovery_badge(db, user):
    for i in range(15):
        db.add(DailyWellness(user_id=user.id, day=date.today() - timedelta(days=i),
                             body_battery_low=40))
    db.commit()
    assert "recovery" in gamification.recompute(db, user).badges_json


def test_recovery_badge_broken_by_one_bad_day(db, user):
    for i in range(15):
        low = 10 if i == 7 else 40
        db.add(DailyWellness(user_id=user.id, day=date.today() - timedelta(days=i),
                             body_battery_low=low))
    db.commit()
    assert "recovery" not in gamification.recompute(db, user).badges_json


def test_every_earned_badge_is_defined():
    for badge in gamification.BADGES:
        assert badge.icon and badge.title and badge.description
    assert len({b.key for b in gamification.BADGES}) == len(gamification.BADGES)


# ============================================================================
# Stato e isolamento
# ============================================================================

def test_recompute_is_idempotent(db, user):
    add_run(db, user.id, days_ago=1, km=10)
    db.commit()
    first = gamification.recompute(db, user).total_xp
    second = gamification.recompute(db, user).total_xp
    assert first == second
    assert db.query(GamificationState).count() == 1


def test_longest_streak_is_never_lost(db, user):
    state = gamification.recompute(db, user)
    state.longest_streak = 42
    db.commit()

    add_run(db, user.id, days_ago=0, km=5)
    db.commit()
    assert gamification.recompute(db, user).longest_streak == 42


def test_state_is_per_user(db, user):
    other = User(email="b@x.it", password_hash="h")
    db.add(other)
    db.commit()

    add_run(db, user.id, days_ago=1, km=10)
    db.commit()
    gamification.recompute(db, user)
    gamification.recompute(db, other)

    assert gamification.progress_of(db, user).total_xp > 0
    assert gamification.progress_of(db, other).total_xp == 0


def test_progress_without_state(db, user):
    progress = gamification.progress_of(db, user)
    assert progress.total_xp == 0 and progress.level == "Principiante"
    assert progress.badges == []


def test_progress_resolves_badges(db, user):
    add_run(db, user.id, days_ago=1, km=21.5)
    db.commit()
    gamification.recompute(db, user)

    progress = gamification.progress_of(db, user)
    keys = {b.key for b in progress.badges}
    assert "half" in keys
    assert all(b.icon for b in progress.badges)


def test_unknown_badge_key_is_ignored(db, user):
    gamification.recompute(db, user)
    state = db.query(GamificationState).one()
    state.badges_json = ["first_10k", "distintivo_inventato"]
    db.commit()

    keys = {b.key for b in gamification.progress_of(db, user).badges}
    assert keys == {"first_10k"}


# ============================================================================
# Integrazione
# ============================================================================

def test_pipeline_recomputes_gamification(db, user, monkeypatch):
    from app import pipeline

    add_run(db, user.id, days_ago=0, km=12)
    db.commit()
    monkeypatch.setattr("app.providers.sync_user", lambda d, u: {"activities": 1})

    result = pipeline.run_for_user(db, user)
    assert result.xp > 0 and result.streak >= 1


def test_gamification_failure_does_not_break_the_pipeline(db, user, monkeypatch):
    from app import pipeline

    monkeypatch.setattr("app.providers.sync_user", lambda d, u: {"activities": 1})
    monkeypatch.setattr("app.gamification.recompute",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("rotto")))

    result = pipeline.run_for_user(db, user)
    assert result.synced == {"activities": 1}
    assert any("gamification" in e for e in result.errors)


def test_coach_page_shows_the_strip(logged_client):
    page = logged_client.get("/coach").text
    assert "progress-strip" in page
    assert "Principiante" in page


def test_xp_follows_the_load_not_the_kilometres(db, user):
    """La contraddizione che questo chiude.

    Con gli XP a chilometro, tre ore di bici tranquilla valevano dieci volte
    un fartlek — mentre tutto il resto dell'app spiega che i chilometri non
    descrivono l'allenamento. Adesso la moneta è il carico, la stessa su cui
    poggiano forma, rapporto acuto/cronico e prontezza.
    """
    from datetime import datetime, timedelta

    from app.db.models import Activity
    from app import gamification

    oggi = datetime.now()
    # Stessa distanza, sforzo opposto.
    db.add(Activity(user_id=user.id, source="garmin", external_id=1,
                    activity_type="running", start_time=oggi - timedelta(days=2),
                    duration_sec=3600, distance_m=12000, avg_hr=170))
    db.commit()
    duro = gamification.recompute(db, user).total_xp

    db.query(Activity).delete()
    db.query(gamification.GamificationState).delete()
    db.add(Activity(user_id=user.id, source="garmin", external_id=2,
                    activity_type="running", start_time=oggi - timedelta(days=2),
                    duration_sec=3600, distance_m=12000, avg_hr=115))
    db.commit()
    facile = gamification.recompute(db, user).total_xp

    assert duro > facile


def test_a_declared_effort_earns_xp_without_a_heart_rate_strap(db, user):
    """Chi non ha la fascia non deve restare al minimo sindacale."""
    from datetime import datetime, timedelta

    from app.db.models import Activity
    from app import gamification

    oggi = datetime.now()
    db.add(Activity(user_id=user.id, source="strava", external_id=3,
                    activity_type="running", start_time=oggi - timedelta(days=1),
                    duration_sec=3600, distance_m=10000, rpe=9))
    db.commit()
    con_rpe = gamification.recompute(db, user).total_xp

    db.query(Activity).delete()
    db.query(gamification.GamificationState).delete()
    db.add(Activity(user_id=user.id, source="strava", external_id=4,
                    activity_type="running", start_time=oggi - timedelta(days=1),
                    duration_sec=3600, distance_m=10000))
    db.commit()
    senza = gamification.recompute(db, user).total_xp

    assert con_rpe > senza
