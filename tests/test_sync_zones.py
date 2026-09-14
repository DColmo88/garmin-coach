"""Scaricamento del tempo per zona FC.

È una chiamata per attività: il test presidia che non se ne facciano più del
necessario e che una risposta strana non fermi la sincronizzazione.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.db.models import Activity, User
from app.garmin import sync


@pytest.fixture()
def user(db) -> User:
    u = User(email="a@x.it", password_hash="h")
    db.add(u)
    db.commit()
    return u


def _add_activities(db, user, count: int, avg_hr: float | None = 140):
    for i in range(count):
        db.add(Activity(
            user_id=user.id, external_id=9000 + i,
            activity_type="running", avg_hr=avg_hr,
            start_time=datetime(2026, 6, 1) + timedelta(days=i),
        ))
    db.commit()


class FakeClient:
    """Registra quali attività sono state chieste."""

    def __init__(self, response=None):
        self.asked: list[int] = []
        self.response = response if response is not None else [
            {"zoneNumber": 1, "secsInZone": 600},
            {"zoneNumber": 2, "secsInZone": 1800},
            {"zoneNumber": 3, "secsInZone": 300},
        ]

    def get_activity_hr_in_timezones(self, activity_id):
        self.asked.append(activity_id)
        return self.response


def _use(monkeypatch, client):
    monkeypatch.setattr(sync, "get_client", lambda user: client)
    return client


def test_missing_zones_are_filled_with_zero(db, user, monkeypatch):
    """Garmin salta le zone in cui non si è passato tempo: a valle servono cinque valori."""
    _add_activities(db, user, 1)
    _use(monkeypatch, FakeClient())

    assert sync.sync_activity_zones(db, user) == 1

    activity = db.query(Activity).one()
    assert activity.hr_zones_json == [600, 1800, 300, 0, 0]


def test_activities_already_downloaded_are_not_asked_again(db, user, monkeypatch):
    _add_activities(db, user, 3)
    client = _use(monkeypatch, FakeClient())

    sync.sync_activity_zones(db, user)
    assert len(client.asked) == 3

    client.asked.clear()
    assert sync.sync_activity_zones(db, user) == 0
    assert client.asked == []


def test_activities_without_heart_rate_are_skipped(db, user, monkeypatch):
    """Senza FC media non c'è nessun tempo per zona da chiedere."""
    _add_activities(db, user, 2, avg_hr=None)
    client = _use(monkeypatch, FakeClient())

    assert sync.sync_activity_zones(db, user) == 0
    assert client.asked == []


def test_the_number_of_calls_per_sync_is_capped(db, user, monkeypatch):
    """La prima sincronizzazione non deve fare duecento richieste di fila."""
    _add_activities(db, user, 40)
    client = _use(monkeypatch, FakeClient())

    sync.sync_activity_zones(db, user, limit=10)

    assert len(client.asked) == 10


def test_the_most_recent_activities_come_first(db, user, monkeypatch):
    _add_activities(db, user, 5)
    client = _use(monkeypatch, FakeClient())

    sync.sync_activity_zones(db, user, limit=2)

    assert client.asked == [9004, 9003]


def test_an_activity_that_fails_does_not_stop_the_others(db, user, monkeypatch):
    _add_activities(db, user, 3)

    class Flaky(FakeClient):
        def get_activity_hr_in_timezones(self, activity_id):
            self.asked.append(activity_id)
            if activity_id == 9001:
                raise RuntimeError("timeout")
            return self.response

    _use(monkeypatch, Flaky())

    assert sync.sync_activity_zones(db, user) == 2
    assert len(client_zones(db)) == 2


def test_an_unexpected_response_is_ignored_rather_than_saved(db, user, monkeypatch):
    _add_activities(db, user, 1)
    _use(monkeypatch, FakeClient(response={"non": "una lista"}))

    assert sync.sync_activity_zones(db, user) == 0
    assert db.query(Activity).one().hr_zones_json is None


def test_an_empty_list_leaves_the_activity_pending(db, user, monkeypatch):
    """Meglio riprovare alla prossima sync che salvare cinque zeri."""
    _add_activities(db, user, 1)
    _use(monkeypatch, FakeClient(response=[]))

    assert sync.sync_activity_zones(db, user) == 0
    assert db.query(Activity).one().hr_zones_json is None


def test_no_pending_activities_means_no_login(db, user, monkeypatch):
    """Senza niente da scaricare non si tocca nemmeno Garmin."""
    def explode(user):
        raise AssertionError("il client non doveva essere creato")

    monkeypatch.setattr(sync, "get_client", explode)
    assert sync.sync_activity_zones(db, user) == 0


def client_zones(db) -> list:
    return [a for a in db.query(Activity).all() if a.hr_zones_json]
