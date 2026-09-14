"""Il fornitore Strava: OAuth, token, normalizzazione, sincronizzazione.

Nessun test tocca la rete: `stravalib.Client` viene sostituito da un finto.
Quello che si prova qui è **il nostro codice** — la conversione del vocabolario,
la rotazione del refresh token, la scrittura delle righe — non la libreria.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.auth.security import decrypt_secret, encrypt_secret
from app.db.models import Activity, ProviderConnection, User
from app.providers import strava


@pytest.fixture()
def user(db) -> User:
    u = User(email="a@x.it", password_hash="h")
    db.add(u)
    db.flush()
    db.add(ProviderConnection(
        user_id=u.id, provider="strava", external_id="4242",
        secret_encrypted=encrypt_secret("refresh-vecchio"),
        access_token_encrypted=encrypt_secret("access-valido"),
        token_expires_at=datetime.utcnow() + timedelta(hours=3),
    ))
    db.commit()
    db.refresh(u)
    return u


@pytest.fixture(autouse=True)
def strava_configured(monkeypatch):
    monkeypatch.setattr("app.config.settings.STRAVA_CLIENT_ID", "123")
    monkeypatch.setattr("app.config.settings.STRAVA_CLIENT_SECRET", "segreto")


class FakeActivity:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeClient:
    """Il minimo di `stravalib.Client` che questo codice usa davvero.

    Quello che deve restituire sta sugli attributi **di classe**: il codice
    sotto prova costruisce il proprio client quando gli pare, e un test non può
    preparare l'istanza giusta senza conoscerne i dettagli interni.
    """

    activities: list["FakeActivity"] = []
    zones_by_id: dict[int, list] = {}
    refreshed_with: list[str] = []
    asked_after: list = []

    def __init__(self, access_token=None, **kw):
        self.access_token = access_token

    def get_activities(self, after=None, before=None, limit=None):
        FakeClient.asked_after.append(after)
        return list(FakeClient.activities)

    def get_activity_zones(self, activity_id):
        if activity_id not in FakeClient.zones_by_id:
            raise RuntimeError("nessuna zona per questa attività")
        return FakeClient.zones_by_id[activity_id]

    def refresh_access_token(self, client_id, client_secret, refresh_token):
        FakeClient.refreshed_with.append(refresh_token)
        return {
            "access_token": "access-nuovo",
            "refresh_token": "refresh-nuovo",
            "expires_at": int((datetime.utcnow() + timedelta(hours=6)).timestamp()),
        }


@pytest.fixture()
def fake_strava(monkeypatch):
    FakeClient.activities = []
    FakeClient.zones_by_id = {}
    FakeClient.refreshed_with = []
    FakeClient.asked_after = []
    monkeypatch.setattr("stravalib.Client", FakeClient)
    return FakeClient


# ============================================================================
# Il vocabolario: Strava parla CamelCase, Garmin snake_case
# ============================================================================

@pytest.mark.parametrize("raw,expected", [
    ("Run", "run"),
    ("TrailRun", "trail_run"),
    ("VirtualRide", "virtual_ride"),
    ("MountainBikeRide", "mountain_bike_ride"),
    ("EBikeRide", "e_bike_ride"),
    ("WeightTraining", "weight_training"),
    ("AlpineSki", "alpine_ski"),
])
def test_sport_names_are_converted(raw, expected):
    assert strava.normalise_sport(raw) == expected


def test_the_converted_names_land_on_the_right_sport():
    """Senza la conversione ogni attività Strava finirebbe in «Altro».

    `app/sports.py` cerca per sottostringa su minuscolo: `TrailRun` non
    incontrerebbe mai `trail_run`, e l'icona e l'unità di misura sarebbero
    entrambe sbagliate.
    """
    from app import sports

    sport, label = sports.classify(strava.normalise_sport("MountainBikeRide"))
    assert sport.family == sports.CYCLING and label == "MTB"
    assert sport.uses_speed is True  # km/h, non minuti al km

    sport, label = sports.classify(strava.normalise_sport("TrailRun"))
    assert sport.family == sports.RUNNING and label == "Trail"


def test_a_missing_sport_stays_none():
    assert strava.normalise_sport(None) is None


# ============================================================================
# I numeri, qualunque cosa la libreria restituisca
# ============================================================================

def test_durations_arrive_as_seconds_whatever_the_shape():
    assert strava._num(timedelta(minutes=50)) == 3000.0
    assert strava._num(3000) == 3000.0
    assert strava._num("3000") == 3000.0


def test_quantities_are_unwrapped():
    class Quantity:
        magnitude = 10000.0

    assert strava._num(Quantity()) == 10000.0


def test_nonsense_becomes_none():
    assert strava._num(None) is None
    assert strava._num("parecchio") is None


def test_dates_lose_their_timezone():
    """Nel database le date stanno tutte senza fuso: mescolarle rompe i confronti."""
    aware = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
    assert strava._naive(aware) == datetime(2026, 8, 15, 12, 0)
    assert strava._naive(aware).tzinfo is None


# ============================================================================
# I token
# ============================================================================

def test_a_valid_token_is_reused(db, user, fake_strava):
    """Rinnovare a ogni chiamata sarebbe una richiesta sprecata ogni volta."""
    assert strava._fresh_token(db, user.connection) == "access-valido"
    assert fake_strava.refreshed_with == []


def test_an_expired_token_is_refreshed_and_saved(db, user, fake_strava):
    user.connection.token_expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()

    assert strava._fresh_token(db, user.connection) == "access-nuovo"
    assert decrypt_secret(user.connection.access_token_encrypted) == "access-nuovo"


def test_the_rotated_refresh_token_is_persisted(db, user, fake_strava):
    """Strava **ruota** il refresh token: se non si salva quello nuovo, il
    collegamento muore in silenzio al rinnovo successivo."""
    user.connection.token_expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()

    strava._fresh_token(db, user.connection)

    assert decrypt_secret(user.connection.secret_encrypted) == "refresh-nuovo"


def test_a_token_about_to_expire_is_refreshed_early(db, user, fake_strava):
    """Un token valido per due minuti scadrebbe a metà sincronizzazione."""
    user.connection.token_expires_at = datetime.utcnow() + timedelta(minutes=2)
    db.commit()

    assert strava._fresh_token(db, user.connection) == "access-nuovo"


def test_a_revoked_authorisation_is_recorded(db, user, monkeypatch, fake_strava):
    """Se l'utente revoca l'app, lo stato lo dice invece di lasciare i dati fermi."""
    def boom(self, **kw):
        raise RuntimeError("invalid refresh token")

    monkeypatch.setattr(FakeClient, "refresh_access_token", boom)
    user.connection.token_expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()

    with pytest.raises(strava.StravaError, match="revocato"):
        strava._fresh_token(db, user.connection)

    assert user.connection.status == "auth_failed"


def test_without_credentials_strava_is_not_offered(monkeypatch):
    monkeypatch.setattr("app.config.settings.STRAVA_CLIENT_ID", "")

    with pytest.raises(strava.StravaError, match="non è configurato"):
        strava.authorization_url("http://x/cb", "stato")


# ============================================================================
# Sincronizzazione delle attività
# ============================================================================

def _activity(**kw):
    base = dict(
        id=555, name="Giro serale", sport_type="Ride",
        start_date_local=datetime(2026, 8, 14, 18, 0),
        moving_time=timedelta(minutes=90), elapsed_time=timedelta(minutes=100),
        distance=45000.0, average_heartrate=138.0, max_heartrate=165.0,
        average_speed=8.3, total_elevation_gain=420.0,
        average_cadence=84.0, average_watts=195.0, calories=900.0,
    )
    base.update(kw)
    return FakeActivity(**base)


def test_an_activity_becomes_a_row(db, user, fake_strava):
    fake_strava.activities = [_activity()]

    count = strava.sync_activities(db, user, user.connection)

    row = db.query(Activity).one()
    assert count == 1
    assert row.source == "strava" and row.external_id == 555
    assert row.activity_type == "ride"        # convertito, non «Ride»
    assert row.duration_sec == 5400           # moving_time, non elapsed
    assert row.distance_m == 45000.0
    assert row.avg_power == 195.0


def test_moving_time_is_preferred_to_elapsed(db, user, fake_strava):
    """Le soste al semaforo non sono allenamento, e il carico si calcola sul tempo vero."""
    fake_strava.activities = [
        _activity(moving_time=timedelta(minutes=60), elapsed_time=timedelta(minutes=95))
    ]

    strava.sync_activities(db, user, user.connection)

    assert db.query(Activity).one().duration_sec == 3600


def test_resyncing_updates_instead_of_duplicating(db, user, fake_strava):
    for name in ("Giro serale", "Giro serale (rinominato)"):
        fake_strava.activities = [_activity(name=name)]
        strava.sync_activities(db, user, user.connection)

    assert db.query(Activity).count() == 1
    assert db.query(Activity).one().name == "Giro serale (rinominato)"


def test_training_effect_stays_empty(db, user, fake_strava):
    """È una metrica Garmin. Lasciarla vuota è più onesto che stimarla."""
    fake_strava.activities = [_activity()]

    strava.sync_activities(db, user, user.connection)

    row = db.query(Activity).one()
    assert row.aerobic_te is None and row.anaerobic_te is None


def test_an_activity_without_an_id_is_skipped(db, user, fake_strava):
    fake_strava.activities = [_activity(id=None)]

    assert strava.sync_activities(db, user, user.connection) == 0
    assert db.query(Activity).count() == 0


# ============================================================================
# Zone di frequenza cardiaca
# ============================================================================

class FakeZone:
    def __init__(self, kind, seconds):
        self.type = kind
        self.distribution_buckets = [FakeActivity(time=s) for s in seconds]


def test_heart_rate_zones_are_stored(db, user, fake_strava):
    db.add(Activity(user_id=user.id, source="strava", external_id=555,
                    activity_type="ride", start_time=datetime(2026, 8, 14),
                    duration_sec=3600, avg_hr=140))
    db.commit()

    fake_strava.zones_by_id = {
        555: [FakeZone("power", [1, 2, 3, 4, 5]),
              FakeZone("heartrate", [600, 900, 1200, 500, 200])]
    }

    assert strava.sync_activity_zones(db, user, user.connection) == 1
    assert db.query(Activity).one().hr_zones_json == [600, 900, 1200, 500, 200]


def test_missing_zones_do_not_stop_the_sync(db, user, fake_strava):
    """Capita: attività senza dati di zona, o senza abbonamento Strava."""
    db.add(Activity(user_id=user.id, source="strava", external_id=777,
                    activity_type="ride", start_time=datetime(2026, 8, 14),
                    duration_sec=3600, avg_hr=140))
    db.commit()

    fake_strava.zones_by_id = {}

    assert strava.sync_activity_zones(db, user, user.connection) == 0
    assert db.query(Activity).one().hr_zones_json is None


def test_activities_without_heart_rate_are_not_asked_about(db, user, fake_strava):
    """Una chiamata per attività: non si spende su chi non ha battiti registrati."""
    db.add(Activity(user_id=user.id, source="strava", external_id=888,
                    activity_type="walk", start_time=datetime(2026, 8, 14),
                    duration_sec=3600, avg_hr=None))
    db.commit()

    assert strava.sync_activity_zones(db, user, user.connection) == 0
