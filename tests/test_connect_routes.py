"""Il collegamento della sorgente dati, dal browser.

`/connect` è il secondo passo dopo la registrazione e l'unico posto dell'app in
cui si dichiara che Garmin e Strava non danno le stesse cose. Da lì in poi
l'interfaccia si limita a nascondere quello che non c'è.
"""
from __future__ import annotations

from urllib.parse import unquote

from sqlalchemy import select

from app.auth.security import decrypt_secret
from app.db.models import ProviderConnection, SleepRecord, User

PASSWORD = "password-di-prova"


def _location(response) -> str:
    """L'indirizzo di ritorno, leggibile: i messaggi viaggiano percent-encoded."""
    return unquote(response.headers["location"])


def _connection(test_db) -> ProviderConnection | None:
    session = test_db()
    try:
        return session.scalar(select(ProviderConnection))
    finally:
        session.close()


# ============================================================================
# Dove si finisce dopo la registrazione
# ============================================================================

def test_a_fresh_account_lands_on_connect(registered_client):
    """Il secondo passo non si scopre da soli: ci si finisce dentro."""
    assert registered_client.get("/login").headers["location"] == "/connect"


def test_the_app_says_it_has_nothing_to_show_yet(registered_client):
    body = registered_client.get("/coach").text

    assert "Non hai ancora collegato una sorgente dati" in body
    assert "/connect" in body


def test_with_a_source_connected_the_banner_is_gone(logged_client):
    assert "Non hai ancora collegato" not in logged_client.get("/coach").text


def test_someone_already_connected_goes_to_the_coach(logged_client):
    assert logged_client.get("/login").headers["location"] == "/coach"


# ============================================================================
# La schermata di scelta
# ============================================================================

def test_the_page_compares_the_two_sources(registered_client):
    body = registered_client.get("/connect").text

    assert "Garmin" in body and "Strava" in body
    assert "Sonno" in body
    assert "Prontezza giornaliera" in body


def test_the_page_says_why_strava_gives_less(registered_client):
    """Spiegare il motivo vale più di elencare cosa manca."""
    body = registered_client.get("/connect").text

    assert "non misura niente" in body
    assert "il sonno e il recupero non li ha proprio" in body


def test_the_page_says_only_one_at_a_time(logged_client):
    assert "una sola" in logged_client.get("/connect").text


def test_strava_is_hidden_when_the_server_has_no_credentials(registered_client, monkeypatch):
    """Meglio non mostrare una scelta che poi non si può completare."""
    monkeypatch.setattr("app.config.settings.STRAVA_CLIENT_ID", "")
    body = registered_client.get("/connect").text

    assert "non è configurato su questo server" in body
    assert 'href="/connect/strava"' not in body


def test_connect_is_protected(client):
    assert client.get("/connect").status_code == 303


# ============================================================================
# Garmin
# ============================================================================

def test_connecting_starts_the_first_sync_by_itself(registered_client):
    """Arrivare su un'app vuota dopo aver collegato l'orologio la fa sembrare rotta.

    Il collegamento porta al coach con il segnale che `app.js` raccoglie per
    far partire la sincronizzazione: nessuno deve scoprire da solo che esiste
    un bottone da premere.
    """
    response = registered_client.post("/connect/garmin", data={
        "email": "atleta@garmin.it", "password": "segreta",
    })

    assert response.status_code == 303
    assert response.headers["location"] == "/coach?nuova_sorgente=1"


def test_a_failed_connection_does_not_start_a_sync(registered_client):
    """Non c'è niente da sincronizzare se il collegamento non è riuscito."""
    response = registered_client.post("/connect/garmin", data={
        "email": "atleta@garmin.it", "password": "wrong",
    })

    assert "nuova_sorgente" not in response.headers["location"]


def test_garmin_is_connected_and_the_password_is_encrypted(registered_client, test_db):
    response = registered_client.post("/connect/garmin", data={
        "email": "atleta@garmin.it", "password": "segreta",
    })

    assert response.status_code == 303
    conn = _connection(test_db)
    assert conn.provider == "garmin"
    assert conn.external_id == "atleta@garmin.it"
    assert conn.secret_encrypted != "segreta"
    assert decrypt_secret(conn.secret_encrypted) == "segreta"


def test_garmin_refuses_wrong_credentials(registered_client, test_db):
    """La verifica è un login vero: meglio saperlo adesso che alle 6:30."""
    response = registered_client.post("/connect/garmin", data={
        "email": "atleta@garmin.it", "password": "wrong",
    })

    assert response.status_code == 303
    assert "error=" in _location(response)
    assert _connection(test_db) is None


def test_connecting_garmin_brings_back_the_health_pages(registered_client, test_db):
    assert registered_client.get("/sleep").status_code == 303  # prima non esiste

    registered_client.post("/connect/garmin", data={
        "email": "atleta@garmin.it", "password": "segreta",
    })

    assert registered_client.get("/sleep").status_code == 200


# ============================================================================
# Strava
# ============================================================================

def test_strava_sends_you_to_strava(registered_client, monkeypatch):
    monkeypatch.setattr("app.config.settings.STRAVA_CLIENT_ID", "123")
    monkeypatch.setattr("app.config.settings.STRAVA_CLIENT_SECRET", "segreto")
    monkeypatch.setattr("app.providers.strava.authorization_url",
                        lambda uri, state: f"https://www.strava.com/oauth?state={state}")

    response = registered_client.get("/connect/strava")

    assert response.status_code == 303
    assert response.headers["location"].startswith("https://www.strava.com/oauth")


def test_a_forged_state_is_refused(registered_client, test_db):
    """Il `state` firmato è ciò che impedisce di far atterrare
    un'autorizzazione altrui su questo account."""
    response = registered_client.get(
        "/connect/strava/callback?code=abc&state=me-lo-sono-inventato"
    )

    assert response.status_code == 303
    assert "non valida" in _location(response)
    assert _connection(test_db) is None


def test_a_state_signed_for_someone_else_is_refused(registered_client, test_db):
    from app.routers.connect import _state_serializer

    other = _state_serializer().dumps({"uid": 9999})
    response = registered_client.get(f"/connect/strava/callback?code=abc&state={other}")

    assert "non appartiene al tuo account" in _location(response)
    assert _connection(test_db) is None


def test_cancelling_on_strava_is_not_a_failure(registered_client):
    """Premere «Annulla» è una scelta, non un guasto."""
    response = registered_client.get("/connect/strava/callback?error=access_denied")

    assert response.status_code == 303
    assert "annullata" in _location(response).lower()


def test_a_valid_callback_stores_the_tokens(registered_client, test_db, monkeypatch):
    from app.routers.connect import _state_serializer

    session = test_db()
    user_id = session.scalar(select(User.id))
    session.close()

    captured = {}

    def fake_connect(db, user, code):
        captured["code"] = code
        conn = ProviderConnection(user_id=user.id, provider="strava",
                                  external_id="4242", secret_encrypted="cifrato")
        db.add(conn)
        db.commit()
        return conn

    monkeypatch.setattr("app.providers.strava.connect", fake_connect)

    state = _state_serializer().dumps({"uid": user_id})
    response = registered_client.get(f"/connect/strava/callback?code=il-codice&state={state}")

    assert response.status_code == 303
    assert captured["code"] == "il-codice"
    assert _connection(test_db).provider == "strava"
    # Anche da Strava la prima sincronizzazione parte da sola.
    assert response.headers["location"] == "/coach?nuova_sorgente=1"


# ============================================================================
# Strava: l'app si rimpicciolisce, non si rompe
# ============================================================================

def test_a_strava_user_has_no_health_group(strava_client):
    body = strava_client.get("/coach").text

    assert ">Salute<" not in body
    assert 'href="/sleep"' not in body


def test_the_health_pages_redirect_instead_of_apologising(strava_client):
    """Nessuna pagina vuota con una spiegazione: quelle pagine non esistono."""
    for path in ("/sleep", "/health", "/body"):
        response = strava_client.get(path)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/coach"


def test_the_training_pages_work_normally(strava_client):
    """Il carico lo calcoliamo noi dalle attività: qui Strava non toglie niente."""
    for path in ("/coach", "/activities", "/fitness", "/plan", "/goals"):
        assert strava_client.get(path).status_code == 200, path


def test_the_coach_leads_with_the_form_instead_of_readiness(strava_client):
    body = strava_client.get("/coach").text

    assert "La tua forma" in body
    assert "Readiness" not in body


def test_the_coach_hides_the_wellness_cards(strava_client):
    """«0/5 in range» sembrerebbe un risultato pessimo invece che un'assenza."""
    body = strava_client.get("/coach").text

    assert "Health Monitor" not in body
    assert "in range" not in body


def test_history_from_a_previous_source_stays_reachable(strava_client, test_db):
    """Chi passa da Garmin a Strava non perde sei mesi di sonno dal menu."""
    from datetime import date

    session = test_db()
    user_id = session.scalar(select(User.id))
    session.add(SleepRecord(user_id=user_id, day=date(2026, 3, 1), sleep_score=80))
    session.commit()
    session.close()

    assert strava_client.get("/sleep").status_code == 200


# ============================================================================
# Scollegare
# ============================================================================

def test_disconnecting_keeps_the_data(logged_client, logged_user, test_db):
    """I dati sono dell'utente, non del fornitore: cambiare orologio non li cancella."""
    from datetime import date

    session = test_db()
    session.add(SleepRecord(user_id=logged_user.id, day=date(2026, 3, 1), sleep_score=80))
    session.commit()
    session.close()

    response = logged_client.post("/connect/disconnect")

    assert response.status_code == 303
    assert _connection(test_db) is None

    session = test_db()
    assert session.query(SleepRecord).count() == 1
    session.close()


def test_disconnecting_without_a_connection_does_not_crash(registered_client):
    assert registered_client.post("/connect/disconnect").status_code == 303


def test_disconnect_is_protected(client):
    assert client.post("/connect/disconnect").status_code == 303


# ============================================================================
# Il segnale arriva davvero al browser
# ============================================================================

def test_the_page_carries_the_code_that_starts_the_sync(logged_client):
    """Il segnale è inutile se la pagina non porta il codice che lo raccoglie."""
    body = logged_client.get("/coach?nuova_sorgente=1").text

    assert "app.js" in body


def test_the_sync_trigger_exists_in_the_javascript():
    """Presidia il patto fra la rotta e il browser: due nomi che devono combaciare."""
    from pathlib import Path

    js = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"
    source = js.read_text()

    assert "nuova_sorgente" in source, "la rotta manda un segnale che nessuno raccoglie"
    assert "runSync" in source
    # Senza ripulire l'indirizzo, un F5 rifarebbe partire la sincronizzazione.
    assert "replaceState" in source


def test_the_sync_button_keeps_its_icon():
    """Scrivere su textContent del bottone cancellava l'SVG dell'icona."""
    from pathlib import Path

    js = Path(__file__).resolve().parent.parent / "app" / "static" / "app.js"
    source = js.read_text()

    assert "syncBtn.textContent" not in source
    assert "syncLabel" in source
