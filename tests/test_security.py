"""Le protezioni che valgono per tutta l'app.

Ogni test qui dentro presidia un buco che c'era davvero: non sono ipotesi, sono
la descrizione di cosa succedeva prima.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.db.models import Activity, DailyCoachCache, SleepRecord, User
from tests.conftest import TEST_EMAIL, TEST_PASSWORD


def _user(test_db) -> User:
    session = test_db()
    try:
        return session.scalar(select(User).where(User.email == TEST_EMAIL))
    finally:
        session.close()


# ============================================================================
# Sessioni revocabili
# ============================================================================


def test_a_disabled_account_loses_access_immediately(logged_client, test_db):
    """Disattivare deve avere effetto adesso, non fra trenta giorni.

    `is_active` era controllato solo nel login: chi aveva già un cookie
    continuava a navigare — e a consumare quota AI — fino alla scadenza
    naturale del cookie.
    """
    assert logged_client.get("/coach").status_code == 200

    session = test_db()
    try:
        user = session.scalar(select(User).where(User.email == TEST_EMAIL))
        user.is_active = False
        user.session_epoch += 1
        session.commit()
    finally:
        session.close()

    response = logged_client.get("/coach")
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_raising_the_epoch_invalidates_the_cookie(logged_client, test_db):
    """È il meccanismo su cui poggia ogni revoca."""
    assert logged_client.get("/coach").status_code == 200

    session = test_db()
    try:
        user = session.scalar(select(User).where(User.email == TEST_EMAIL))
        user.session_epoch += 1
        session.commit()
    finally:
        session.close()

    assert logged_client.get("/coach").status_code == 303


def test_changing_the_password_keeps_this_session_and_drops_the_others(
    logged_client, test_db
):
    """Chi cambia la password resta dentro; gli altri dispositivi no."""
    cookie_prima = logged_client.cookies.get("gc_session")

    response = logged_client.post("/settings/password", data={
        "current_password": TEST_PASSWORD,
        "new_password": "una-password-nuova",
        "new_password_confirm": "una-password-nuova",
    })
    assert response.status_code == 303
    assert "pw_ok" in response.headers["location"]

    # Il cookie è stato riemesso, quindi l'utente prosegue senza rifare l'accesso…
    assert logged_client.get("/coach").status_code == 200
    cookie_dopo = logged_client.cookies.get("gc_session")
    assert cookie_dopo != cookie_prima

    # …ma quello vecchio, che un altro dispositivo avrebbe ancora, non vale più.
    logged_client.cookies.set("gc_session", cookie_prima)
    assert logged_client.get("/coach").status_code == 303


def test_admin_deactivation_revokes_the_sessions(logged_client, test_db):
    """Il bottone «Disattiva» di /admin alza l'epoca, non solo il flag."""
    session = test_db()
    try:
        admin = User(email="admin@x.it", password_hash="h", is_admin=True)
        session.add(admin)
        session.commit()
        admin_id, target_id = admin.id, _user(test_db).id
    finally:
        session.close()

    from app.auth.session import create_session_token

    session = test_db()
    try:
        admin = session.get(User, admin_id)
        token = create_session_token(admin)
    finally:
        session.close()

    logged_client.cookies.set("gc_session", token)
    assert logged_client.post(f"/admin/users/{target_id}/toggle").status_code == 303

    session = test_db()
    try:
        target = session.get(User, target_id)
        assert target.is_active is False
        assert target.session_epoch == 1
    finally:
        session.close()


# ============================================================================
# Il rubinetto AI di /coach?day=
# ============================================================================


@pytest.fixture()
def briefing_spy(monkeypatch):
    """Conta le volte in cui l'app si prepara a parlare col modello.

    Il briefing si costruisce **solo** subito prima di una chiamata AI, quindi
    contare le costruzioni conta le chiamate senza dover simulare un provider.
    """
    calls: list[date] = []

    def fake_build(db, user, today=None):
        calls.append(today or date.today())
        return "briefing finto"

    monkeypatch.setattr("app.ai.briefing.build", fake_build)
    return calls


def test_a_past_day_never_calls_the_model(logged_client, briefing_spy):
    """`?day=` era spesa illimitata: ogni data nuova valeva tre chiamate."""
    ieri = (date.today() - timedelta(days=1)).isoformat()
    assert logged_client.get(f"/coach?day={ieri}").status_code == 200
    assert briefing_spy == []


def test_a_past_day_does_not_leave_a_cache_row(logged_client, test_db):
    """Nessuna riga per le date passate: sarebbe una riga per ogni URL digitato."""
    ieri = date.today() - timedelta(days=1)
    logged_client.get(f"/coach?day={ieri.isoformat()}")

    session = test_db()
    try:
        rows = session.scalars(
            select(DailyCoachCache).where(DailyCoachCache.day == ieri)
        ).all()
        assert list(rows) == []
    finally:
        session.close()


@pytest.mark.parametrize("raw", [
    "2999-01-01",       # il futuro non esiste
    "1990-01-01",       # oltre il limite all'indietro
    "non-una-data",     # spazzatura
    "2026-13-45",       # data impossibile
])
def test_an_out_of_range_day_falls_back_to_today(raw):
    from app.clock import today_for
    from app.routers.pages import _requested_day

    utente = User(email="a@x.it", password_hash="h", timezone="Europe/Rome")
    assert _requested_day(raw, utente) == today_for(utente)


def test_a_day_inside_the_window_is_honoured():
    from app.clock import today_for
    from app.routers.pages import MAX_DAYS_BACK, _requested_day

    utente = User(email="a@x.it", password_hash="h", timezone="Europe/Rome")
    dentro = today_for(utente) - timedelta(days=MAX_DAYS_BACK - 1)
    assert _requested_day(dentro.isoformat(), utente) == dentro


def test_the_unmetered_plan_endpoint_is_gone(logged_client):
    """`POST /ai/plan` generava un piano senza quota e senza registrarne il costo."""
    assert logged_client.post("/ai/plan", data={"goal": "x"}).status_code == 404


# ============================================================================
# Limite di frequenza
# ============================================================================


def test_repeated_logins_are_throttled(client):
    from app.security import RULES

    limite = RULES["POST /login"].limit
    dati = {"email": "ignoto@x.it", "password": "sbagliata"}

    for _ in range(limite):
        assert client.post("/login", data=dati).status_code == 200

    rifiutata = client.post("/login", data=dati)
    assert rifiutata.status_code == 429
    assert "Retry-After" in rifiutata.headers


def test_the_limit_does_not_spill_onto_other_routes(client):
    from app.security import RULES

    dati = {"email": "ignoto@x.it", "password": "sbagliata"}
    for _ in range(RULES["POST /login"].limit + 3):
        client.post("/login", data=dati)

    # `/register` ha il suo contatore: chi sbaglia il login non deve trovarsi
    # sbarrata anche la registrazione.
    assert client.get("/register").status_code == 200


def test_outside_production_the_forwarded_header_is_ignored(client):
    """In sviluppo l'app risponde da sola: quell'intestazione non ha autorità.

    Crederle senza un proxy davanti vuol dire che per non avere alcun limite
    basta cambiarla a ogni richiesta.
    """
    from app.security import RULES

    dati = {"email": "ignoto@x.it", "password": "sbagliata"}
    for i in range(RULES["POST /login"].limit):
        client.post("/login", data=dati, headers={"X-Forwarded-For": f"10.0.0.{i}"})

    rifiutata = client.post(
        "/login", data=dati, headers={"X-Forwarded-For": "10.0.0.99"}
    )
    assert rifiutata.status_code == 429


def test_in_production_the_last_forwarded_value_wins(client, monkeypatch):
    """Caddy aggiunge in coda: l'ultimo valore l'ha scritto lui, non il client.

    Chi prova a nascondersi mettendo un indirizzo suo davanti si ritrova
    contato lo stesso, perché il proprio finisce comunque in fondo.
    """
    from app.config import settings
    from app.security import RULES

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://garmin.example.org")
    dati = {"email": "ignoto@x.it", "password": "sbagliata"}

    for i in range(RULES["POST /login"].limit):
        client.post(
            "/login", data=dati,
            headers={"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.7"},
        )

    rifiutata = client.post(
        "/login", data=dati,
        headers={"X-Forwarded-For": "10.0.0.99, 203.0.113.7"},
    )
    assert rifiutata.status_code == 429

    # Un indirizzo davvero diverso, invece, ha il suo contatore.
    altro = client.post(
        "/login", data=dati,
        headers={"X-Forwarded-For": "198.51.100.4"},
    )
    assert altro.status_code == 200


# ============================================================================
# Intestazioni e Content-Security-Policy
# ============================================================================


def test_every_page_carries_a_csp(logged_client):
    response = logged_client.get("/coach")
    csp = response.headers["content-security-policy"]

    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    # È il punto della CSP: uno script inline senza nonce non gira, quindi un
    # markup iniettato non diventa codice eseguito.
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


def test_the_inline_scripts_carry_the_nonce_of_their_page(logged_client):
    response = logged_client.get("/")
    csp = response.headers["content-security-policy"]

    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert f'nonce="{nonce}"' in response.text


def test_chartjs_is_served_from_here(logged_client):
    """Era l'unico script di terze parti, e arrivava senza `integrity`."""
    response = logged_client.get("/")
    assert "cdn.jsdelivr.net" not in response.text
    assert "/static/chart.umd.min.js" in response.text


def test_the_usual_headers_are_there(logged_client):
    headers = logged_client.get("/coach").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "geolocation=()" in headers["permissions-policy"]


# ============================================================================
# Configurazione
# ============================================================================


def test_production_refuses_to_start_with_the_development_secret(monkeypatch):
    from app.config import DEV_SESSION_SECRET, ConfigError, settings

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://garmin.example.org")
    monkeypatch.setattr(settings, "SESSION_SECRET", DEV_SESSION_SECRET)
    monkeypatch.setattr(settings, "FERNET_KEY", "qualcosa")

    with pytest.raises(ConfigError):
        settings.validate()


def test_production_refuses_to_start_without_a_fernet_key(monkeypatch):
    from app.config import ConfigError, settings

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://garmin.example.org")
    monkeypatch.setattr(settings, "SESSION_SECRET", "un-segreto-vero")
    monkeypatch.setattr(settings, "FERNET_KEY", "")

    with pytest.raises(ConfigError):
        settings.validate()


def test_development_only_warns(monkeypatch):
    """Bloccare `uvicorn --reload` per una chiave mancante sarebbe una seccatura."""
    from app.config import DEV_SESSION_SECRET, settings

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000")
    monkeypatch.setattr(settings, "SESSION_SECRET", DEV_SESSION_SECRET)
    monkeypatch.setattr(settings, "FERNET_KEY", "")

    settings.validate()  # non solleva


def test_the_cookie_is_secure_only_in_production(monkeypatch):
    from fastapi.responses import RedirectResponse

    from app.auth.session import set_session_cookie
    from app.config import settings

    user = User(id=1, email="a@x.it", password_hash="h", session_epoch=0)

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://garmin.example.org")
    prod = RedirectResponse("/")
    set_session_cookie(prod, user)
    assert "Secure" in prod.headers["set-cookie"]

    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000")
    dev = RedirectResponse("/")
    set_session_cookie(dev, user)
    assert "Secure" not in dev.headers["set-cookie"]


# ============================================================================
# Cancellazione dell'account
# ============================================================================


def test_deleting_an_account_takes_the_data_with_it(logged_client, test_db):
    user = _user(test_db)
    session = test_db()
    try:
        session.add_all([
            Activity(user_id=user.id, external_id=1, name="Corsa",
                     activity_type="running"),
            SleepRecord(user_id=user.id, day=date.today(), sleep_score=80),
        ])
        session.commit()
    finally:
        session.close()

    response = logged_client.post(
        "/settings/delete-account", data={"confirm_email": TEST_EMAIL}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/login"

    session = test_db()
    try:
        assert session.get(User, user.id) is None
        assert session.scalars(
            select(Activity).where(Activity.user_id == user.id)
        ).all() == []
        assert session.scalars(
            select(SleepRecord).where(SleepRecord.user_id == user.id)
        ).all() == []
    finally:
        session.close()


def test_deleting_requires_typing_the_email(logged_client, test_db):
    user_id = _user(test_db).id

    response = logged_client.post(
        "/settings/delete-account", data={"confirm_email": "qualcos@altro.it"}
    )
    assert response.status_code == 303
    assert "pw_error" in response.headers["location"]

    session = test_db()
    try:
        assert session.get(User, user_id) is not None
    finally:
        session.close()


def test_deleting_removes_the_garmin_tokens_from_disk(logged_client, test_db):
    from pathlib import Path

    from app.config import settings

    user_id = _user(test_db).id
    tokenstore = Path(settings.GARMIN_TOKENSTORE) / str(user_id)
    tokenstore.mkdir(parents=True, exist_ok=True)
    (tokenstore / "oauth1_token.json").write_text("{}")

    logged_client.post("/settings/delete-account", data={"confirm_email": TEST_EMAIL})

    # I token Garmin vivono fuori dal database: senza rimuoverli resterebbero
    # lì le credenziali di sessione di un account che non esiste più.
    assert not tokenstore.exists()


def test_the_database_would_cascade_on_its_own(db):
    """Cintura e bretelle: la cancellazione è esplicita **e** le FK hanno CASCADE.

    Il cascade copre le tabelle che qualcuno aggiungerà dopo dimenticandosi di
    metterle nell'elenco di `delete_account`.
    """
    user = User(email="cascata@x.it", password_hash="h")
    db.add(user)
    db.flush()
    db.add(Activity(user_id=user.id, external_id=99, activity_type="running"))
    db.commit()

    db.delete(user)
    db.commit()

    assert db.scalars(
        select(Activity).where(Activity.external_id == 99)
    ).all() == []
