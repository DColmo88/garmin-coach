"""Le capacità dei fornitori, e cosa ne segue per l'interfaccia.

La regola di prodotto che questi test presidiano: **quello che un utente non
può avere, per lui non esiste.** Niente pagine vuote, niente «dato non
disponibile» ripetuto a ogni schermata. Chi usa Strava vede un'app più piccola
e intera, non un'app grande e rotta.
"""
from __future__ import annotations

from datetime import date

import pytest

from app import navigation, providers
from app.db.models import (
    BodyComposition,
    DailyWellness,
    ProviderConnection,
    SleepRecord,
    User,
)


def _user(db, provider: str | None) -> User:
    u = User(email=f"{provider or 'nessuno'}@x.it", password_hash="h")
    db.add(u)
    db.flush()
    if provider:
        db.add(ProviderConnection(user_id=u.id, provider=provider,
                                  external_id="x", secret_encrypted="cifrato"))
    db.commit()
    db.refresh(u)
    return u


# ============================================================================
# Capacità
# ============================================================================

def test_garmin_measures_everything(db):
    caps = providers.capabilities(_user(db, "garmin"))

    assert providers.Capability.SLEEP in caps
    assert providers.Capability.WELLNESS in caps
    assert providers.Capability.BODY in caps
    assert providers.Capability.ACTIVITIES in caps


def test_strava_only_has_the_workouts(db):
    """Strava non è un dispositivo: di notte non misura niente."""
    caps = providers.capabilities(_user(db, "strava"))

    assert caps == frozenset({providers.Capability.ACTIVITIES, providers.Capability.ZONES})
    assert providers.Capability.SLEEP not in caps


def test_without_a_connection_there_are_no_capabilities(db):
    assert providers.capabilities(_user(db, None)) == frozenset()


def test_provider_of_returns_none_when_nothing_is_connected(db):
    assert providers.provider_of(_user(db, None)) is None


# ============================================================================
# Sezioni visibili
# ============================================================================

def test_a_garmin_user_sees_the_health_group(db):
    sections = providers.visible_sections(db, _user(db, "garmin"))

    assert {"sleep", "health", "body", "readiness"} <= sections


def test_a_strava_user_does_not(db):
    sections = providers.visible_sections(db, _user(db, "strava"))

    assert "sleep" not in sections
    assert "health" not in sections
    assert "body" not in sections
    assert "readiness" not in sections


def test_history_survives_a_change_of_source(db):
    """Sei mesi di sonno registrati con Garmin non spariscono passando a Strava.

    Restano consultabili e smettono di aggiornarsi: cancellarli dalla vista
    perché si è cambiato orologio vorrebbe dire perdere anni di storico per un
    cambio di attrezzo.
    """
    user = _user(db, "strava")
    db.add(SleepRecord(user_id=user.id, day=date(2026, 3, 1), sleep_score=80))
    db.add(BodyComposition(user_id=user.id, day=date(2026, 3, 1), weight_g=72000))
    db.commit()

    sections = providers.visible_sections(db, user)

    assert "sleep" in sections and "body" in sections
    # ...ma non sono più alimentate, ed è una cosa diversa dal vederle vive.
    assert providers.is_live(user, "sleep") is False
    assert providers.is_live(user, "activities") is True


def test_a_section_the_provider_feeds_is_live(db):
    user = _user(db, "garmin")
    db.add(DailyWellness(user_id=user.id, day=date(2026, 3, 1), resting_hr=50))
    db.commit()

    assert providers.is_live(user, "health") is True


def test_visible_sections_costs_a_single_query(db):
    """Gira a ogni pagina per disegnare il menu: quattro query sarebbero quattro di troppo."""
    from sqlalchemy import event

    user = _user(db, "garmin")
    engine = db.get_bind()
    queries: list[str] = []

    def record(conn, cursor, statement, *rest):
        queries.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        providers.sections_with_history(db, user)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert len(queries) == 1, f"{len(queries)} query per disegnare una barra laterale"


# ============================================================================
# Il menu che ne discende
# ============================================================================

def test_the_health_group_disappears_entirely(db):
    """Un gruppo rimasto senza voci sparisce con la sua intestazione.

    «Salute» seguito dal nulla sarebbe peggio che non averlo.
    """
    groups = navigation.groups_for(providers.visible_sections(db, _user(db, "strava")))

    assert "Salute" not in [g.label for g in groups]
    assert {"Oggi", "Allenamento"} <= {g.label for g in groups}


def test_the_training_group_is_untouched(db):
    """Il carico lo calcoliamo noi dalle attività: Strava non toglie niente qui."""
    groups = {g.label: g for g in navigation.groups_for(
        providers.visible_sections(db, _user(db, "strava"))
    )}

    keys = {i.key for i in groups["Allenamento"].items}
    assert keys == {"activities", "fitness", "plan", "goals"}


def test_a_garmin_user_gets_the_whole_menu(db):
    groups = navigation.groups_for(providers.visible_sections(db, _user(db, "garmin")))

    assert [g.label for g in groups] == ["Oggi", "Allenamento", "Salute"]


def test_the_data_source_is_the_first_account_entry(db):
    """È la cosa che si configura per prima e, se manca, rende inutile il resto."""
    assert navigation.ACCOUNT_ITEMS[0].url == "/connect"


# ============================================================================
# Sincronizzazione
# ============================================================================

def test_sync_without_a_connection_does_nothing(db):
    """Registrato ma non ancora collegato è uno stato normale, non un guasto."""
    assert providers.sync_user(db, _user(db, None)) == {}


def test_sync_dispatches_to_the_connected_provider(db, monkeypatch):
    user = _user(db, "strava")
    monkeypatch.setattr("app.providers.strava.sync",
                        lambda d, u, c: {"activities": 7})

    assert providers.sync_user(db, user) == {"activities": 7}


def test_an_unknown_provider_is_refused(db):
    user = _user(db, "garmin")
    user.connection.provider = "polar"
    db.commit()

    with pytest.raises(ValueError, match="sconosciuto"):
        providers.sync_user(db, user)


# ============================================================================
# Il vincolo di una sorgente sola
# ============================================================================

def test_one_source_per_user(db):
    """Due insieme darebbero la stessa corsa due volte: Strava importa da Garmin."""
    from sqlalchemy.exc import IntegrityError

    user = _user(db, "garmin")
    db.add(ProviderConnection(user_id=user.id, provider="strava",
                              external_id="999", secret_encrypted="x"))

    with pytest.raises(IntegrityError):
        db.commit()
