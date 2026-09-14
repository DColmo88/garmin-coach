"""Il profilo fisiologico: dichiarato, osservato, stimato."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.analysis.profile import (
    DECLARED,
    DEFAULT_HR_MAX,
    ESTIMATED,
    OBSERVED,
    resolve_profile,
)
from app.db.models import Activity, DailyWellness, User


@pytest.fixture()
def user(db) -> User:
    u = User(email="a@x.it", password_hash="h")
    db.add(u)
    db.commit()
    return u


def _activity(user_id: int, day: date, max_hr: float | None = None, **kw) -> Activity:
    return Activity(
        user_id=user_id,
        external_id=int(day.strftime("%Y%m%d")) * 10 + kw.pop("n", 0),
        activity_type=kw.pop("activity_type", "running"),
        start_time=datetime.combine(day, datetime.min.time()),
        max_hr=max_hr,
        **kw,
    )


def test_declared_values_win(db, user):
    user.hr_max, user.hr_rest, user.lthr, user.ftp = 195, 44, 172, 260
    db.commit()

    profile = resolve_profile(db, user)

    assert (profile.hr_max, profile.hr_rest, profile.lthr, profile.ftp) == (195, 44, 172, 260)
    assert profile.sources["hr_max"] == DECLARED
    assert profile.is_fully_declared is False  # il sesso resta non dichiarato


def test_hr_max_from_the_highest_peak_ever_recorded(db, user):
    today = date.today()
    db.add_all([
        _activity(user.id, today - timedelta(days=3), max_hr=178, n=1),
        _activity(user.id, today - timedelta(days=10), max_hr=196, n=2),
        _activity(user.id, today - timedelta(days=20), max_hr=181, n=3),
    ])
    db.commit()

    profile = resolve_profile(db, user)

    assert profile.hr_max == 196
    assert profile.sources["hr_max"] == OBSERVED


def test_implausible_peaks_are_ignored(db, user):
    """Un'attività con picco a 80 bpm non ha misurato uno sforzo."""
    today = date.today()
    db.add(_activity(user.id, today, max_hr=80, n=1))
    db.commit()

    profile = resolve_profile(db, user)

    assert profile.hr_max == DEFAULT_HR_MAX
    assert profile.sources["hr_max"] == ESTIMATED


def test_old_peaks_do_not_count(db, user):
    """La massima di due anni fa non descrive il cuore di adesso."""
    db.add(_activity(user.id, date.today() - timedelta(days=800), max_hr=200, n=1))
    db.commit()

    assert resolve_profile(db, user).sources["hr_max"] == ESTIMATED


def test_hr_max_from_age_when_there_are_no_activities(db, user):
    user.birth_year = date.today().year - 40
    db.commit()

    profile = resolve_profile(db, user)

    assert profile.hr_max == round(211 - 0.64 * 40)
    assert profile.sources["hr_max"] == ESTIMATED


def test_hr_rest_is_the_median_not_the_mean(db, user):
    """Una notte con l'orologio slacciato non deve spostare il valore."""
    today = date.today()
    values = [50, 51, 52, 53, 120]  # l'ultimo è un errore di misura
    for i, rhr in enumerate(values):
        db.add(DailyWellness(user_id=user.id, day=today - timedelta(days=i), resting_hr=rhr))
    db.commit()

    profile = resolve_profile(db, user)

    assert profile.hr_rest == 52
    assert profile.sources["hr_rest"] == OBSERVED


def test_lthr_is_estimated_from_hr_max(db, user):
    user.hr_max = 200
    db.commit()

    profile = resolve_profile(db, user)

    assert profile.lthr == 176  # 88% di 200
    assert profile.sources["lthr"] == ESTIMATED


def test_resting_hr_can_never_exceed_max(db, user):
    """Con pochi dati le due stime possono incrociarsi: il profilo resta sensato."""
    user.hr_max = 100
    today = date.today()
    for i in range(5):
        db.add(DailyWellness(user_id=user.id, day=today - timedelta(days=i), resting_hr=150))
    db.commit()

    profile = resolve_profile(db, user)

    assert profile.hr_rest < profile.hr_max
    assert profile.hr_reserve > 0


def test_trimp_coefficient_depends_on_sex(db, user):
    user.sex = "f"
    db.commit()
    assert resolve_profile(db, user).trimp_k == pytest.approx(1.67)

    user.sex = "m"
    db.commit()
    assert resolve_profile(db, user).trimp_k == pytest.approx(1.92)


def test_unknown_sex_falls_back_without_crashing(db, user):
    profile = resolve_profile(db, user)
    assert profile.trimp_k > 0
    assert profile.sources["sex"] == ESTIMATED
