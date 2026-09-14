"""Pagina impostazioni: preferenze, iscrizione push, collegamento Telegram."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import NotificationLog, PushSubscription, User


def current_user(test_db) -> User:
    session = test_db()
    try:
        return session.scalar(select(User).where(User.email == "test@x.it"))
    finally:
        session.close()


# ============================================================================
# Pagina
# ============================================================================

def test_settings_requires_login(client):
    assert client.get("/settings").status_code == 303


def test_settings_page_lists_events_and_channels(logged_client):
    page = logged_client.get("/settings").text
    assert "Rischio di sovrallenamento" in page
    assert "Sonno in peggioramento" in page
    assert "Email" in page and "Telegram" in page


def test_unconfigured_channels_are_shown_as_such(logged_client):
    """Senza SMTP/Telegram/VAPID i canali si vedono ma sono spenti."""
    page = logged_client.get("/settings").text
    assert "Non configurato sul server" in page


def test_defaults_are_reflected_in_the_form(logged_client):
    """L'utente che non ha mai salvato vede i default delle regole."""
    page = logged_client.get("/settings").text
    # overtraining è on di default, readiness_high no
    overtraining = page.split('name="event_overtraining"')[1][:80]
    readiness_high = page.split('name="event_readiness_high"')[1][:80]
    assert "checked" in overtraining
    assert "checked" not in readiness_high


# ============================================================================
# Salvataggio delle preferenze
# ============================================================================

def test_saving_preferences(logged_client, test_db):
    response = logged_client.post("/settings/notifications", data={
        "event_overtraining": "on",
        "event_readiness_high": "on",
        "channel_email": "on",
    })
    assert response.status_code == 303
    assert response.headers["location"] == "/settings?saved=1"

    user = current_user(test_db)
    assert user.notify_prefs_json["events"]["overtraining"] is True
    assert user.notify_prefs_json["events"]["readiness_high"] is True
    assert user.notify_prefs_json["events"]["sleep_decline"] is False
    assert user.notify_prefs_json["channels"]["email"] is True
    assert user.notify_prefs_json["channels"]["telegram"] is False


def test_unchecking_everything_disables_everything(logged_client, test_db):
    logged_client.post("/settings/notifications", data={})
    user = current_user(test_db)
    assert not any(user.notify_prefs_json["events"].values())
    assert not any(user.notify_prefs_json["channels"].values())


def test_saved_preferences_survive_a_reload(logged_client):
    logged_client.post("/settings/notifications", data={"event_readiness_high": "on"})
    page = logged_client.get("/settings").text
    readiness_high = page.split('name="event_readiness_high"')[1][:80]
    overtraining = page.split('name="event_overtraining"')[1][:80]
    assert "checked" in readiness_high
    assert "checked" not in overtraining


def test_preferences_do_not_wipe_other_keys(logged_client, test_db):
    """Il codice di collegamento Telegram non deve sparire salvando le notifiche."""
    logged_client.post("/settings/telegram/link")
    logged_client.post("/settings/notifications", data={"event_overtraining": "on"})

    user = current_user(test_db)
    assert "telegram_link_code" in user.notify_prefs_json
    assert user.notify_prefs_json["events"]["overtraining"] is True


# ============================================================================
# Notifica di prova
# ============================================================================

def test_test_notification_without_channels(logged_client):
    response = logged_client.post("/settings/test-notification")
    assert response.status_code == 200
    assert response.json() == {"sent": 0}


def test_test_notification_uses_the_active_channels(logged_client, test_db, monkeypatch):
    class FakeChannel:
        key, label, is_configured = "finto", "Finto", True

        def __init__(self):
            self.sent = []

        def is_available_for(self, db, user):
            return True

        def send(self, db, user, notification):
            self.sent.append(notification)
            return True

    channel = FakeChannel()
    monkeypatch.setattr("app.notifications.dispatcher.CHANNELS", {"finto": channel})

    assert logged_client.post("/settings/test-notification").json() == {"sent": 1}
    assert channel.sent[0].title == "Notifica di prova"

    session = test_db()
    assert session.query(NotificationLog).count() == 1
    session.close()


# ============================================================================
# Web Push
# ============================================================================

SUBSCRIPTION = {
    "endpoint": "https://push.example.com/abc",
    "keys": {"p256dh": "chiave-pubblica", "auth": "segreto"},
}


def test_push_subscribe_stores_the_subscription(logged_client, test_db):
    assert logged_client.post("/settings/push/subscribe", json=SUBSCRIPTION).status_code == 200

    session = test_db()
    row = session.query(PushSubscription).one()
    assert row.endpoint == SUBSCRIPTION["endpoint"]
    assert row.p256dh == "chiave-pubblica"
    session.close()


def test_push_subscribe_is_idempotent(logged_client, test_db):
    """Ri-registrare lo stesso browser aggiorna, non duplica."""
    logged_client.post("/settings/push/subscribe", json=SUBSCRIPTION)
    updated = {**SUBSCRIPTION, "keys": {"p256dh": "nuova", "auth": "nuovo"}}
    logged_client.post("/settings/push/subscribe", json=updated)

    session = test_db()
    row = session.query(PushSubscription).one()
    assert row.p256dh == "nuova"
    session.close()


def test_push_subscribe_rejects_malformed_payload(logged_client, test_db):
    response = logged_client.post("/settings/push/subscribe", json={"endpoint": "x"})
    assert response.status_code == 400

    session = test_db()
    assert session.query(PushSubscription).count() == 0
    session.close()


def test_push_unsubscribe(logged_client, test_db):
    logged_client.post("/settings/push/subscribe", json=SUBSCRIPTION)
    logged_client.post("/settings/push/unsubscribe",
                       json={"endpoint": SUBSCRIPTION["endpoint"]})

    session = test_db()
    assert session.query(PushSubscription).count() == 0
    session.close()


def test_push_endpoints_require_login(client):
    assert client.post("/settings/push/subscribe", json=SUBSCRIPTION).status_code == 303


def test_a_user_cannot_remove_someone_elses_subscription(logged_client, test_db):
    session = test_db()
    intruder = User(email="altro@x.it", password_hash="h")
    session.add(intruder)
    session.commit()
    session.add(PushSubscription(user_id=intruder.id, endpoint=SUBSCRIPTION["endpoint"],
                                 p256dh="k", auth="a"))
    session.commit()
    session.close()

    logged_client.post("/settings/push/unsubscribe",
                       json={"endpoint": SUBSCRIPTION["endpoint"]})

    session = test_db()
    assert session.query(PushSubscription).count() == 1  # quella dell'altro resta
    session.close()


# ============================================================================
# Telegram
# ============================================================================

def test_telegram_link_returns_a_stable_code(logged_client):
    first = logged_client.post("/settings/telegram/link").json()["code"]
    second = logged_client.post("/settings/telegram/link").json()["code"]
    assert first and first == second  # non se ne genera uno nuovo ogni volta


def test_telegram_unlink_clears_the_chat_id(logged_client, test_db):
    session = test_db()
    user = session.scalar(select(User).where(User.email == "test@x.it"))
    user.telegram_chat_id = "12345"
    session.commit()
    session.close()

    logged_client.post("/settings/telegram/unlink")
    assert current_user(test_db).telegram_chat_id is None


# ============================================================================
# PWA
# ============================================================================

def test_manifest_is_served(logged_client):
    response = logged_client.get("/static/manifest.json")
    assert response.status_code == 200
    assert response.json()["short_name"] == "Coach"


def test_service_worker_is_served(logged_client):
    response = logged_client.get("/static/sw.js")
    assert response.status_code == 200
    assert "showNotification" in response.text


def test_icons_exist(logged_client):
    for size in (192, 512):
        assert logged_client.get(f"/static/icon-{size}.png").status_code == 200


def test_pages_declare_the_manifest(logged_client):
    page = logged_client.get("/coach").text
    assert 'rel="manifest"' in page
    assert 'apple-touch-icon' in page


# ============================================================================
# Webhook Telegram: chiude il collegamento
# ============================================================================

@pytest.fixture()
def telegram(monkeypatch):
    """Bot configurato e chiamate uscenti intercettate."""
    monkeypatch.setattr("app.config.settings.TELEGRAM_BOT_TOKEN", "123456:token-finto")
    replies = []
    monkeypatch.setattr(
        "httpx.post",
        lambda url, **kw: replies.append(kw.get("json", {})) or _FakeResponse(),
    )
    return replies


class _FakeResponse:
    status_code = 200
    text = "{}"


def webhook_url() -> str:
    from app.config import settings as config

    return f"/telegram/webhook/{config.telegram_webhook_secret}"


def start_payload(code: str = "", chat_id: int = 555) -> dict:
    text = f"/start {code}".strip()
    return {"message": {"chat": {"id": chat_id}, "text": text}}


def test_webhook_links_the_account(logged_client, test_db, telegram):
    code = logged_client.post("/settings/telegram/link").json()["code"]

    response = logged_client.post(webhook_url(), json=start_payload(code))
    assert response.status_code == 200

    user = current_user(test_db)
    assert user.telegram_chat_id == "555"
    assert "Collegato" in telegram[0]["text"]


def test_link_code_is_single_use(logged_client, test_db, telegram):
    code = logged_client.post("/settings/telegram/link").json()["code"]
    logged_client.post(webhook_url(), json=start_payload(code))

    # lo stesso codice non collega un secondo dispositivo
    logged_client.post(webhook_url(), json=start_payload(code, chat_id=999))
    assert current_user(test_db).telegram_chat_id == "555"


def test_webhook_rejects_an_unknown_code(logged_client, test_db, telegram):
    logged_client.post(webhook_url(), json=start_payload("inventato"))
    assert current_user(test_db).telegram_chat_id is None
    assert "non valido" in telegram[0]["text"]


def test_webhook_explains_a_bare_start(logged_client, telegram):
    logged_client.post(webhook_url(), json=start_payload())
    assert "impostazioni" in telegram[0]["text"].lower()


def test_webhook_ignores_other_messages(logged_client, telegram):
    payload = {"message": {"chat": {"id": 555}, "text": "ciao come stai"}}
    assert logged_client.post(webhook_url(), json=payload).json() == {"ok": True}
    assert telegram == []


def test_webhook_survives_a_malformed_payload(logged_client, telegram):
    assert logged_client.post(webhook_url(), json={"garbage": True}).json() == {"ok": True}


def test_webhook_rejects_a_wrong_secret(logged_client, test_db, telegram):
    code = logged_client.post("/settings/telegram/link").json()["code"]
    response = logged_client.post("/telegram/webhook/segreto-sbagliato",
                                  json=start_payload(code))
    assert response.status_code == 404
    assert current_user(test_db).telegram_chat_id is None


def test_webhook_is_closed_when_the_bot_is_not_configured(client, monkeypatch):
    monkeypatch.setattr("app.config.settings.TELEGRAM_BOT_TOKEN", "")
    assert client.post("/telegram/webhook/qualsiasi", json={}).status_code == 404


def test_webhook_secret_is_stable_and_not_the_token(monkeypatch):
    from app.config import settings as config

    monkeypatch.setattr("app.config.settings.TELEGRAM_BOT_TOKEN", "123456:token-finto")
    secret = config.telegram_webhook_secret
    assert secret == config.telegram_webhook_secret  # deterministico
    assert len(secret) == 32
    assert "token-finto" not in secret


# ============================================================================
# Cambio password
# ============================================================================

NEW_PASSWORD = "una-nuova-password"


def test_the_page_offers_the_password_change(logged_client):
    body = logged_client.get("/settings").text

    assert 'action="/settings/password"' in body
    # È la password dell'app, non quella dell'orologio: la pagina lo dice.
    assert "non quella del tuo orologio" in body


def test_the_page_shows_which_source_is_connected(logged_client):
    body = logged_client.get("/settings").text

    assert "Sorgente dati" in body
    assert "Garmin" in body


def test_the_password_can_be_changed(logged_client, test_db):
    from app.auth import service

    response = logged_client.post("/settings/password", data={
        "current_password": "password-di-prova",
        "new_password": NEW_PASSWORD,
        "new_password_confirm": NEW_PASSWORD,
    })

    assert response.status_code == 303
    session = test_db()
    try:
        assert service.login(session, "test@x.it", NEW_PASSWORD)
    finally:
        session.close()


def test_the_current_password_is_required(logged_client, test_db):
    from urllib.parse import unquote

    from app.auth import service

    response = logged_client.post("/settings/password", data={
        "current_password": "non-e-questa",
        "new_password": NEW_PASSWORD,
        "new_password_confirm": NEW_PASSWORD,
    })

    assert "non è corretta" in unquote(response.headers["location"])
    session = test_db()
    try:
        assert service.login(session, "test@x.it", "password-di-prova")
    finally:
        session.close()


def test_the_two_new_passwords_must_match(logged_client):
    from urllib.parse import unquote

    response = logged_client.post("/settings/password", data={
        "current_password": "password-di-prova",
        "new_password": NEW_PASSWORD,
        "new_password_confirm": "un-altra-ancora",
    })

    assert "non coincidono" in unquote(response.headers["location"])


def test_a_short_new_password_is_refused(logged_client):
    from urllib.parse import unquote

    response = logged_client.post("/settings/password", data={
        "current_password": "password-di-prova",
        "new_password": "corta", "new_password_confirm": "corta",
    })

    assert "almeno" in unquote(response.headers["location"])


def test_the_password_route_is_protected(client):
    assert client.post("/settings/password", data={}).status_code == 303
