"""I tool della chat: aggregazione, tetti sulle righe, isolamento fra utenti."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.ai import tools
from app.db.models import (
    Activity,
    BodyComposition,
    DailyWellness,
    SleepRecord,
    TrainingMetric,
    User,
)


@pytest.fixture()
def user(db) -> User:
    u = User(email="a@x.it", password_hash="h")
    db.add(u)
    db.commit()
    return u


def seed_days(db, user_id: int, days: int, end: date | None = None) -> None:
    end = end or date.today()
    for i in range(days):
        day = end - timedelta(days=i)
        db.add(DailyWellness(user_id=user_id, day=day, total_steps=9000 + i,
                             resting_hr=52, avg_stress=30,
                             body_battery_high=90, body_battery_low=25))
        db.add(SleepRecord(user_id=user_id, day=day, total_sleep_sec=7 * 3600,
                           deep_sleep_sec=5400, rem_sleep_sec=4500, sleep_score=80,
                           resting_hr=50))
        db.add(TrainingMetric(user_id=user_id, day=day, vo2max=52.0, training_load=250,
                              hrv_weekly_avg=65, training_status="productive",
                              hrv_status="balanced"))
    db.commit()


# ============================================================================
# Registro
# ============================================================================

def test_every_tool_has_an_implementation():
    for name, (spec, impl) in tools.TOOLS.items():
        assert spec.name == name
        assert callable(impl)


def test_tool_specs_have_object_schemas():
    for spec in tools.tool_specs():
        assert spec.parameters["type"] == "object"
        assert spec.description


def test_unknown_tool_returns_message_instead_of_raising(db, user):
    execute = tools.make_executor(db, user.id)
    assert "inesistente" in execute("get_password", {})


# ============================================================================
# Aggregazione: le serie lunghe non escono grezze
# ============================================================================

def test_short_period_is_daily(db, user):
    seed_days(db, user.id, 7)
    out = tools.tool_get_wellness(db, user.id, {})
    assert out.count("\n") == 7  # intestazione + 7 giorni
    assert "medie settimanali" not in out


def test_medium_period_collapses_to_weeks(db, user):
    seed_days(db, user.id, 90)
    out = tools.tool_get_wellness(
        db, user.id,
        {"date_from": (date.today() - timedelta(days=89)).isoformat()},
    )
    assert "medie settimanali" in out
    # 90 giorni → ~13 settimane, non 90 righe
    assert out.count("\n") <= 15


def test_long_period_collapses_to_months(db, user):
    seed_days(db, user.id, 300)
    out = tools.tool_get_wellness(
        db, user.id,
        {"date_from": (date.today() - timedelta(days=299)).isoformat()},
    )
    assert "medie mensili" in out
    assert out.count("\n") <= 12


def test_output_never_exceeds_the_row_cap(db, user):
    seed_days(db, user.id, 300)
    for fn in (tools.tool_get_wellness, tools.tool_get_sleep, tools.tool_get_performance):
        out = fn(db, user.id, {"date_from": (date.today() - timedelta(days=299)).isoformat()})
        assert out.count("\n") <= tools.MAX_ROWS


def test_long_period_output_stays_small(db, user):
    """Un anno di dati deve restare un contesto piccolo, non un dump."""
    seed_days(db, user.id, 365)
    out = tools.tool_get_sleep(
        db, user.id, {"date_from": (date.today() - timedelta(days=364)).isoformat()}
    )
    assert len(out) < 2000, f"output di {len(out)} caratteri: troppo per il contesto"


# ============================================================================
# Contenuto dei singoli tool
# ============================================================================

def test_wellness_reports_values(db, user):
    seed_days(db, user.id, 3)
    out = tools.tool_get_wellness(db, user.id, {})
    assert "FC riposo 52" in out and "Body Battery 25–90" in out


def test_sleep_reports_hours_and_deep_percentage(db, user):
    seed_days(db, user.id, 3)
    out = tools.tool_get_sleep(db, user.id, {})
    assert "7.0h" in out and "score 80" in out
    assert "profondo 21%" in out  # 5400 / 25200


def test_activities_list_with_pace_and_ids(db, user):
    db.add(Activity(user_id=user.id, external_id=555, activity_type="running",
                    start_time=datetime.now() - timedelta(days=1),
                    distance_m=10000, duration_sec=3000, avg_hr=150))
    db.commit()

    out = tools.tool_get_activities(db, user.id, {})
    assert "[id 555]" in out
    assert "10.0 km" in out
    assert "5:00/km" in out
    assert "Corsa" in out, "il codice Garmin non deve arrivare al modello"
    assert "1 allenamenti" in out


def test_a_ride_is_listed_in_kmh_not_in_minutes_per_km(db, user):
    """«100 km a 2:35/km» è giusto e illeggibile, anche per il modello."""
    db.add(Activity(user_id=user.id, external_id=556, activity_type="cycling",
                    start_time=datetime.now() - timedelta(days=1),
                    distance_m=60000, duration_sec=9000, avg_hr=140))
    db.commit()

    out = tools.tool_get_activities(db, user.id, {})

    assert "24,0 km/h" in out
    assert "/km" not in out.replace("km/h", "")
    assert "Bici" in out


def test_activities_filter_by_type(db, user):
    now = datetime.now()
    db.add(Activity(user_id=user.id, external_id=1, activity_type="running",
                    start_time=now, distance_m=5000, duration_sec=1500))
    db.add(Activity(user_id=user.id, external_id=2, activity_type="cycling",
                    start_time=now, distance_m=30000, duration_sec=3600))
    db.commit()

    running = tools.tool_get_activities(db, user.id, {"activity_type": "running"})
    assert "[id 1]" in running and "[id 2]" not in running


def test_activity_detail(db, user):
    db.add(Activity(user_id=user.id, external_id=77, name="Lungo domenicale",
                    activity_type="running", start_time=datetime(2026, 8, 2, 8, 0),
                    distance_m=21097, duration_sec=7200, avg_hr=155, max_hr=178,
                    elevation_gain_m=210, calories=1500, aerobic_te=3.8))
    db.commit()

    out = tools.tool_get_activity_detail(db, user.id, {"activity_id": 77})
    assert "Lungo domenicale" in out
    assert "21.10 km" in out and "02/08/2026" in out and "155/178" in out


def test_activity_detail_rejects_missing_id(db, user):
    assert "id numerico" in tools.tool_get_activity_detail(db, user.id, {})


def test_activity_detail_unknown_id(db, user):
    assert "Nessun allenamento" in tools.tool_get_activity_detail(db, user.id, {"activity_id": 9})


def test_performance_reports_status(db, user):
    seed_days(db, user.id, 5)
    out = tools.tool_get_performance(db, user.id, {})
    assert "VO2max 52.0" in out
    assert "Training status attuale: productive" in out


def test_body_explains_when_there_is_no_scale(db, user):
    out = tools.tool_get_body(db, user.id, {})
    assert "bilancia" in out.lower()


def test_body_reports_weight(db, user):
    db.add(BodyComposition(user_id=user.id, day=date.today(), weight_g=72500,
                           bmi=22.4, body_fat_pct=14.2, muscle_mass_g=34000))
    db.commit()
    out = tools.tool_get_body(db, user.id, {})
    assert "72.5 kg" in out and "14.2%" in out


def test_goal_tool(db, user):
    from app import goals

    assert "Nessun obiettivo" in tools.tool_get_goal(db, user.id, {})
    goals.set_active_goal(db, user, "race_10k", params={"target_time": "45:00"})
    assert "45:00" in tools.tool_get_goal(db, user.id, {})


# ============================================================================
# Confronto fra periodi
# ============================================================================

def test_compare_periods_computes_deltas(db, user):
    today = date.today()
    # 30 giorni recenti: FC a riposo 50. 30 precedenti: 56.
    for i in range(30):
        db.add(DailyWellness(user_id=user.id, day=today - timedelta(days=i),
                             resting_hr=50, total_steps=10000, avg_stress=25))
    for i in range(30, 60):
        db.add(DailyWellness(user_id=user.id, day=today - timedelta(days=i),
                             resting_hr=56, total_steps=8000, avg_stress=35))
    db.commit()

    out = tools.tool_compare_periods(db, user.id, {"days": 30})
    assert "FC a riposo: 50.0 contro 56.0" in out
    assert "↓" in out and "-11%" in out


def test_compare_periods_clamps_the_window(db, user):
    seed_days(db, user.id, 5)
    out = tools.tool_compare_periods(db, user.id, {"days": 9999})
    assert "Ultimi 365 giorni" in out


def test_compare_periods_without_data(db, user):
    out = tools.tool_compare_periods(db, user.id, {"days": 30})
    assert "Ultimi 30 giorni" in out  # non esplode


# ============================================================================
# Isolamento: il modello non può leggere i dati di un altro utente
# ============================================================================

def test_executor_ignores_a_user_id_in_the_arguments(db, user):
    """Anche se il modello passasse user_id, l'esecutore usa il proprio."""
    other = User(email="b@x.it", password_hash="h")
    db.add(other)
    db.commit()

    db.add(Activity(user_id=other.id, external_id=999, activity_type="running",
                    start_time=datetime.now(), distance_m=42195, duration_sec=14400))
    db.commit()

    execute = tools.make_executor(db, user.id)
    out = execute("get_activities", {"user_id": other.id, "date_from": "2020-01-01"})
    assert "[id 999]" not in out
    assert "Nessun dato" in out


def test_activity_detail_cannot_cross_users(db, user):
    other = User(email="b@x.it", password_hash="h")
    db.add(other)
    db.commit()
    db.add(Activity(user_id=other.id, external_id=999, activity_type="running",
                    start_time=datetime.now(), distance_m=10000, duration_sec=3000))
    db.commit()

    out = tools.tool_get_activity_detail(db, user.id, {"activity_id": 999})
    assert "Nessun allenamento" in out


# ============================================================================
# Robustezza degli argomenti (arrivano da un modello, non da un form)
# ============================================================================

def test_malformed_dates_fall_back_to_defaults(db, user):
    seed_days(db, user.id, 7)
    out = tools.tool_get_wellness(db, user.id, {"date_from": "l'anno scorso", "date_to": None})
    assert "Benessere" in out


def test_inverted_range_is_reordered(db, user):
    seed_days(db, user.id, 10)
    out = tools.tool_get_wellness(db, user.id, {
        "date_from": date.today().isoformat(),
        "date_to": (date.today() - timedelta(days=5)).isoformat(),
    })
    assert "Benessere" in out and "Nessun dato" not in out


def test_empty_arguments_work_for_every_tool(db, user):
    seed_days(db, user.id, 3)
    execute = tools.make_executor(db, user.id)
    for name in tools.TOOLS:
        if name == "get_activity_detail":
            continue  # richiede un id per definizione
        assert execute(name, {})
