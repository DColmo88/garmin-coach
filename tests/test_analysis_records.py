"""Record personali calcolati dallo storico."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from app.analysis.records import personal_records


def _run(day_offset: int, km: float, minutes: float, **kw):
    return SimpleNamespace(
        external_id=1000 + day_offset,
        activity_type=kw.pop("activity_type", "running"),
        start_time=datetime(2026, 6, 1) + timedelta(days=day_offset),
        distance_m=km * 1000,
        duration_sec=minutes * 60,
        elevation_gain_m=kw.pop("elevation_gain_m", None),
        **kw,
    )


def _by_key(records, key):
    return next((r for r in records if r.key == key), None)


def test_the_fastest_ten_kilometres_wins():
    records = personal_records([
        _run(0, 10.0, 50),
        _run(1, 10.0, 44),
        _run(2, 10.0, 47),
    ])

    ten = _by_key(records, "dist_10000")
    assert ten.value == "44:00"
    assert ten.day.day == 2  # 1 giugno + 1
    assert not ten.estimated


def test_a_slightly_longer_run_counts_with_the_time_normalised():
    """10,4 km valgono come un 10 km, riportando il tempo alla distanza esatta."""
    records = personal_records([_run(0, 10.4, 52)])

    ten = _by_key(records, "dist_10000")
    assert ten.value == "50:00"
    assert ten.estimated


def test_a_run_too_short_does_not_grant_the_record():
    """Su 9 km non si può rivendicare un primato sui 10."""
    records = personal_records([_run(0, 9.0, 40)])
    assert _by_key(records, "dist_10000") is None


def test_a_run_far_too_long_is_not_scaled_down():
    """Un lungo da 20 km non è un 10 km corso a quel ritmo.

    E non è nemmeno una mezza: 20 km stanno sotto il 97% dei 21,097, cioè
    fuori dalla finestra in cui normalizzare il tempo sarebbe onesto.
    """
    records = personal_records([_run(0, 20.0, 100)])
    assert _by_key(records, "dist_10000") is None
    assert _by_key(records, "dist_21097") is None

    # Una mezza corsa davvero, invece, il primato lo assegna.
    records = personal_records([_run(0, 21.1, 105)])
    assert _by_key(records, "dist_21097") is not None


def test_distance_records_ignore_other_sports():
    records = personal_records([_run(0, 10.0, 20, activity_type="cycling")])
    assert _by_key(records, "dist_10000") is None


def test_volume_records_count_every_sport():
    """La seduta più lunga è la più lunga, non la corsa più lunga."""
    records = personal_records([
        _run(0, 10.0, 50),
        _run(1, 80.0, 180, activity_type="cycling"),
    ])

    assert _by_key(records, "longest").value == "80,0 km"


def test_fastest_pace_skips_the_short_outings():
    """Un chilometro di riscaldamento veloce non è il ritmo migliore."""
    records = personal_records([
        _run(0, 1.0, 3),      # 3:00/km ma solo 1 km
        _run(1, 10.0, 45),    # 4:30/km su 10 km
    ])

    assert _by_key(records, "pace").value == "4:30 /km"


def test_the_best_week_uses_a_rolling_window():
    """Sette giorni consecutivi, non la settimana di calendario."""
    records = personal_records([
        _run(0, 10.0, 50), _run(1, 10.0, 50), _run(2, 10.0, 50),
        _run(5, 15.0, 75), _run(6, 15.0, 75),
    ])

    assert _by_key(records, "week").value == "60,0 km"


def test_hours_are_shown_only_when_there_are_hours():
    records = personal_records([_run(0, 42.2, 210)])
    assert _by_key(records, "dist_42195").value.count(":") == 2


def test_an_empty_history_produces_no_records():
    assert personal_records([]) == []


def test_activities_without_numbers_are_skipped():
    broken = SimpleNamespace(
        external_id=1, activity_type="running",
        start_time=None, distance_m=None, duration_sec=None,
        elevation_gain_m=None,
    )
    assert personal_records([broken]) == []


def test_the_weekly_record_does_not_repeat_its_own_date():
    """Il dettaglio porta già l'intervallo: chi lo mostra non deve riscriverlo."""
    records = personal_records([_run(0, 10.0, 50), _run(1, 10.0, 50)])
    week = _by_key(records, "week")
    assert week.detail_has_date is True


def test_the_other_records_carry_their_date_separately():
    records = personal_records([_run(0, 10.0, 50)])
    assert _by_key(records, "longest").detail_has_date is False
