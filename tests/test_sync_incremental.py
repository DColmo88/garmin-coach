"""La sincronizzazione chiede solo quello che non ha già.

Il problema che questi test presidiano è di volume, non di correttezza: la
versione precedente riscaricava ventotto giorni per tre endpoint ogni notte —
e `sync_training` ne interroga quattro per giorno — cioè circa centosettanta
richieste per utente, ogni notte, per riscrivere con gli stessi valori righe
già in archivio. Su un'API non documentata e legata a un account personale,
il volume è il rischio operativo principale dell'app.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.clock import today_for
from app.db.models import Activity, DailyWellness, SleepRecord, User
from app.garmin import sync as gsync


@pytest.fixture()
def user(db) -> User:
    u = User(email="a@x.it", password_hash="h", timezone="Europe/Rome")
    db.add(u)
    db.commit()
    return u


def test_an_empty_archive_asks_for_the_whole_window(db, user):
    giorni = gsync._days_to_fetch(db, SleepRecord, user, 28)
    assert len(giorni) == 28


def test_days_already_stored_are_not_asked_again(db, user):
    oggi = today_for(user)
    for i in range(28):
        db.add(SleepRecord(user_id=user.id, day=oggi - timedelta(days=i), sleep_score=70))
    db.commit()

    giorni = gsync._days_to_fetch(db, SleepRecord, user, 28)

    # Restano solo i giorni recenti, che Garmin ritocca ancora.
    assert len(giorni) == gsync.REFRESH_DAYS
    assert set(giorni) == set(gsync._last_days(gsync.REFRESH_DAYS, user))


def test_recent_days_are_refetched_even_if_present(db, user):
    """Garmin corregge sonno e riepilogo per un paio di giorni dopo."""
    oggi = today_for(user)
    db.add(SleepRecord(user_id=user.id, day=oggi, sleep_score=70))
    db.commit()

    assert oggi in gsync._days_to_fetch(db, SleepRecord, user, 28)


def test_a_hole_in_the_middle_is_asked_for(db, user):
    oggi = today_for(user)
    for i in range(28):
        if i == 10:
            continue
        db.add(DailyWellness(user_id=user.id, day=oggi - timedelta(days=i), total_steps=5000))
    db.commit()

    giorni = gsync._days_to_fetch(db, DailyWellness, user, 28)

    assert oggi - timedelta(days=10) in giorni
    assert len(giorni) == gsync.REFRESH_DAYS + 1


def test_the_saving_is_the_whole_point(db, user):
    """Il numero che conta: quante richieste in meno fa la sync di stanotte."""
    oggi = today_for(user)
    for i in range(28):
        db.add(SleepRecord(user_id=user.id, day=oggi - timedelta(days=i), sleep_score=70))
    db.commit()

    prima = 28
    dopo = len(gsync._days_to_fetch(db, SleepRecord, user, 28))
    assert dopo <= prima / 5


# ============================================================================
# Attività: recupero dello storico e paginazione
# ============================================================================


class FakeClient:
    """Restituisce attività a pagine, come fa Garmin."""

    def __init__(self, total: int, start_day: date) -> None:
        self.pages_requested: list[tuple[int, int]] = []
        self.activities = [
            {
                "activityId": 1000 + i,
                "activityName": f"Corsa {i}",
                "activityType": {"typeKey": "running"},
                "startTimeLocal": datetime.combine(
                    start_day - timedelta(days=i), datetime.min.time()
                ).strftime("%Y-%m-%d %H:%M:%S"),
                "duration": 3600,
                "distance": 10000,
            }
            for i in range(total)
        ]

    def get_activities(self, start: int, limit: int):
        self.pages_requested.append((start, limit))
        return self.activities[start:start + limit]


def test_the_first_sync_goes_back_a_year_not_fifty_activities(db, user):
    """Chi collega Garmin con anni di storia ne otteneva cinquanta.

    Due mesi scarsi per chi si allena spesso, cioè meno della finestra su cui
    si calcolano le curve di fitness e fatica.
    """
    oggi = today_for(user)
    client = FakeClient(total=300, start_day=oggi)

    presi = gsync._fetch_activities(client, gsync._activity_horizon(db, user))

    assert len(presi) > gsync.ACTIVITY_PAGE
    assert len(client.pages_requested) > 1


def test_a_routine_sync_stops_at_the_newest_already_stored(db, user):
    oggi = today_for(user)
    db.add(Activity(
        user_id=user.id, source="garmin", external_id=1,
        activity_type="running",
        start_time=datetime.combine(oggi - timedelta(days=5), datetime.min.time()),
    ))
    db.commit()

    client = FakeClient(total=300, start_day=oggi)
    gsync._fetch_activities(client, gsync._activity_horizon(db, user))

    # Una pagina sola: le cinquanta più recenti coprono già i cinque giorni
    # che mancano, e non c'è ragione di scavare oltre.
    assert len(client.pages_requested) == 1


def test_the_paging_has_a_ceiling(db, user):
    """Dieci anni di storia non devono diventare mille richieste."""
    oggi = today_for(user)
    client = FakeClient(total=10_000, start_day=oggi)

    gsync._fetch_activities(client, oggi - timedelta(days=99_999))

    assert len(client.pages_requested) == gsync.MAX_ACTIVITY_PAGES
