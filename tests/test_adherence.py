"""Il confronto fra quello che il piano prevedeva e quello che è successo.

Era il pezzo che mancava del tutto: il piano veniva generato una volta e da lì
in poi nessuno guardava più se l'atleta lo stesse seguendo. Il coach non sapeva
che avevi saltato tre sedute, gli insight nemmeno, e `today_session`
continuava a proporre la seduta del giovedì della settimana sei a chi era fermo
da dieci giorni.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.db.models import Activity, TrainingPlan, User
from app.training import WEEKDAYS, adherence, planned_days

LUNEDI = date(2026, 8, 3)   # un lunedì


def _plan(weeks: int = 2) -> TrainingPlan:
    """Un piano con tre sedute a settimana: lunedì, mercoledì, sabato."""
    return TrainingPlan(
        user_id=1,
        start_date=LUNEDI,
        weeks_total=weeks,
        plan_json={
            "title": "Prova",
            "weeks": [
                {
                    "number": n,
                    "days": [
                        {"weekday": "lunedì", "type": "facile", "detail": "8 km"},
                        {"weekday": "martedì", "type": "riposo", "detail": ""},
                        {"weekday": "mercoledì", "type": "intervalli", "detail": "5×1000"},
                        {"weekday": "giovedì", "type": "riposo", "detail": ""},
                        {"weekday": "venerdì", "type": "riposo", "detail": ""},
                        {"weekday": "sabato", "type": "lungo", "detail": "16 km"},
                        {"weekday": "domenica", "type": "riposo", "detail": ""},
                    ],
                }
                for n in range(1, weeks + 1)
            ],
        },
    )


def _activity(day: date) -> Activity:
    return Activity(
        user_id=1, source="garmin", external_id=int(day.strftime("%Y%m%d")),
        activity_type="running",
        start_time=datetime.combine(day, datetime.min.time()),
        duration_sec=3000, distance_m=8000,
    )


def test_rest_days_are_not_sessions_to_miss():
    """Saltare un riposo non è una mancanza."""
    previste = planned_days(_plan(1), LUNEDI + timedelta(days=6))
    assert len(previste) == 3
    assert {d.type for d in previste} == {"facile", "intervalli", "lungo"}


def test_a_perfect_week_is_a_hundred_percent():
    piano = _plan(1)
    fatte = [_activity(LUNEDI + timedelta(days=d)) for d in (0, 2, 5)]

    a = adherence(piano, fatte, today=LUNEDI + timedelta(days=7))

    assert a.total == 3
    assert a.done == 3
    assert a.pct == 100
    assert a.missed == []
    assert not a.is_adrift


def test_missed_sessions_are_named_with_their_day():
    piano = _plan(1)
    fatte = [_activity(LUNEDI)]

    a = adherence(piano, fatte, today=LUNEDI + timedelta(days=7))

    assert a.done == 1
    assert [d.type for d in a.missed] == ["intervalli", "lungo"]
    assert "intervalli" in a.as_line()


def test_todays_session_is_not_counted_as_missed_yet():
    """Alle otto del mattino la seduta di oggi non è saltata: è di oggi."""
    piano = _plan(1)

    a = adherence(piano, [], today=LUNEDI)   # lunedì, giorno di seduta

    assert a.total == 0


def test_extra_sessions_are_counted_separately():
    """Chi si allena più del previsto non ha «aderito meno»."""
    piano = _plan(1)
    fatte = [_activity(LUNEDI + timedelta(days=d)) for d in (0, 1, 2, 5)]

    a = adherence(piano, fatte, today=LUNEDI + timedelta(days=7))

    assert a.pct == 100
    assert a.extra == 1
    assert "1 allenamento in più" in a.as_line()


def test_a_plan_nobody_is_following_says_so():
    piano = _plan(2)
    fatte = [_activity(LUNEDI)]

    a = adherence(piano, fatte, today=LUNEDI + timedelta(days=14))

    assert a.total == 6
    assert a.pct == 17
    assert a.is_adrift


def test_a_couple_of_missed_sessions_is_not_adrift():
    """La soglia non deve scattare su due sedute: è una settimana storta."""
    piano = _plan(2)
    fatte = [_activity(LUNEDI + timedelta(days=d)) for d in (0, 2, 5, 7)]

    a = adherence(piano, fatte, today=LUNEDI + timedelta(days=14))

    assert a.pct == 67
    assert not a.is_adrift


def test_a_plan_without_a_start_date_says_nothing():
    piano = _plan(1)
    piano.start_date = None

    a = adherence(piano, [_activity(LUNEDI)], today=LUNEDI + timedelta(days=7))

    assert a.total == 0
    assert a.pct is None
    assert a.as_line() == ""


def test_only_recent_misses_are_named():
    """Le sedute saltate un mese fa non servono al consiglio di oggi."""
    piano = _plan(4)

    a = adherence(piano, [], today=LUNEDI + timedelta(days=28))

    assert a.total == 12
    assert len(a.recent_missed) < a.total
    # La finestra guarda indietro da ieri, non dall'ultima seduta prevista.
    assert all(d.day >= LUNEDI + timedelta(days=14) for d in a.recent_missed)


def test_a_plan_that_ended_long_ago_has_nothing_recent():
    piano = _plan(1)
    a = adherence(piano, [], today=LUNEDI + timedelta(days=60))

    assert a.missed          # le sedute saltate ci sono ancora
    assert a.recent_missed == []
