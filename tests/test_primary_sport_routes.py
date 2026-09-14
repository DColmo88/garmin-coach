"""La scelta corsa/bici: alla registrazione, nelle impostazioni, e nelle viste.

Il principio: **è una preferenza di lettura, non un filtro.** Cambia cosa si
vede per primo; non toglie niente ai calcoli né al contesto dell'AI, perché una
persona può correre, pedalare e andare in montagna nella stessa settimana.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import select

from app import sports
from app.db.models import Activity, InviteCode, User


def _reload(test_db, email: str) -> User:
    session = test_db()
    try:
        return session.scalar(select(User).where(User.email == email))
    finally:
        session.close()


# ============================================================================
# Registrazione
# ============================================================================

PASSWORD = "password-di-prova"


def _register(client, email, code, sport=""):
    return client.post("/register", data={
        "email": email, "password": PASSWORD, "password_confirm": PASSWORD,
        "invite_code": code, "primary_sport": sport,
    })


def test_the_registration_form_asks_for_the_sport(client):
    body = client.get("/register").text

    assert "Come ti alleni di più?" in body
    assert 'name="primary_sport"' in body
    assert "Corsa" in body and "Bici" in body


def test_the_chosen_sport_is_saved_at_registration(client, test_db):
    session = test_db()
    session.add(InviteCode(code="INV-2"))
    session.commit()
    session.close()

    response = _register(client, "ciclista@x.it", "INV-2", sport="cycling")

    assert response.status_code == 303
    assert _reload(test_db, "ciclista@x.it").primary_sport == "cycling"


def test_an_invented_sport_at_registration_is_ignored(client, test_db):
    session = test_db()
    session.add(InviteCode(code="INV-3"))
    session.commit()
    session.close()

    _register(client, "tale@x.it", "INV-3", sport="parapendio")

    user = _reload(test_db, "tale@x.it")
    assert user.primary_sport is None
    assert sports.primary_sport(user) == sports.RUNNING


# ============================================================================
# Impostazioni
# ============================================================================

def test_the_settings_page_shows_the_current_choice(logged_client):
    body = logged_client.get("/settings").text

    assert "Come ti alleni" in body
    assert 'action="/settings/sport"' in body
    # Non filtra niente, e la pagina lo dice.
    assert "Non filtra niente" in body


def test_the_sport_can_be_changed(logged_client, test_db):
    response = logged_client.post("/settings/sport", data={"primary_sport": "cycling"})

    assert response.status_code == 303
    assert _reload(test_db, "test@x.it").primary_sport == "cycling"


def test_an_invalid_choice_leaves_things_as_they_were(logged_client, test_db):
    logged_client.post("/settings/sport", data={"primary_sport": "cycling"})
    logged_client.post("/settings/sport", data={"primary_sport": "sci_di_fondo"})

    assert _reload(test_db, "test@x.it").primary_sport == "cycling"


def test_the_route_is_protected(client):
    assert client.post("/settings/sport", data={}).status_code == 303


# ============================================================================
# L'effetto sulle viste
# ============================================================================

def _add(session, user_id, kind, days_ago, km, minutes, n=0, **kw):
    session.add(Activity(
        user_id=user_id, external_id=4000 + days_ago * 10 + n,
        activity_type=kind,
        start_time=datetime.combine(date.today() - timedelta(days=days_ago),
                                    datetime.min.time()),
        distance_m=km * 1000, duration_sec=minutes * 60,
        avg_hr=140, max_hr=170, **kw,
    ))


def test_a_cyclist_sees_cycling_records(logged_client, logged_user, test_db, monkeypatch):
    from app.garmin import service

    monkeypatch.setattr(service, "get_performance_snapshot", lambda user: None)

    session = test_db()
    for i in range(4):
        _add(session, logged_user.id, "cycling", i * 3, 60, 150, n=i, avg_power=210)
    _add(session, logged_user.id, "running", 1, 10, 50, n=9)
    session.commit()
    session.close()

    logged_client.post("/settings/sport", data={"primary_sport": "cycling"})
    body = logged_client.get("/fitness").text

    assert "Giro più veloce" in body
    assert "Potenza media più alta" in body
    # Un ciclista non vuole il primato sui 5 km di corsa in cima alla pagina.
    assert "5 KM" not in body.upper().replace("10 KM", "")


def test_a_runner_sees_running_records(logged_client, logged_user, test_db, monkeypatch):
    from app.garmin import service

    monkeypatch.setattr(service, "get_performance_snapshot", lambda user: None)

    session = test_db()
    for i in range(3):
        _add(session, logged_user.id, "running", i * 2, 10, 48 + i, n=i)
    session.commit()
    session.close()

    body = logged_client.get("/fitness").text

    assert "10 km" in body
    assert "Ritmo medio più veloce" in body


def test_the_other_sports_never_disappear_from_the_data(logged_client, logged_user, test_db):
    """Il punto che conta: cambiare vista non toglie niente ai calcoli."""
    from app.ai import briefing
    from app.db.models import User as UserModel

    session = test_db()
    _add(session, logged_user.id, "cycling", 2, 60, 150, n=1)
    _add(session, logged_user.id, "running", 1, 10, 50, n=2)
    _add(session, logged_user.id, "hiking", 4, 8, 140, n=3)
    session.commit()

    user = session.get(UserModel, logged_user.id)
    user.primary_sport = "running"
    session.commit()

    text = briefing.build(session, user)
    session.close()

    # Lo sport principale è dichiarato, ma ci sono tutti.
    assert "corsa" in text.lower()
    assert "Bici" in text
    assert "Escursione" in text
    assert "km/h" in text, "per la bici deve restare la velocità, non il ritmo"
