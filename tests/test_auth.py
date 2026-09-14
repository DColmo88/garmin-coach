"""Autenticazione: crittografia, sessioni, registrazione, accesso, password.

Il modello è cambiato con la v3. Prima le credenziali Garmin *erano* il login,
e da lì discendeva tutto il resto: la registrazione doveva contattare Garmin
per validarle, e il login sapeva rimediare da solo se la password cambiava di
là. Adesso l'account è dell'app, e la sorgente dei dati si collega dopo.

Il test che tiene insieme la modifica è
`test_login_never_touches_a_provider`: se un giorno qualcuno reintroducesse una
verifica remota nel login, un Garmin irraggiungibile impedirebbe di entrare
nella propria app.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import InviteCode, SleepRecord, User

PASSWORD = "password-di-prova"


@pytest.fixture(autouse=True)
def fernet_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("app.config.settings.SESSION_SECRET", "test-secret")


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _invite(db, code: str, **kw) -> None:
    db.add(InviteCode(code=code, **kw))
    db.commit()


# ============================================================================
# Crittografia
# ============================================================================

def test_encrypt_roundtrip():
    from app.auth import security

    token = security.encrypt_secret("password-garmin")
    assert token != "password-garmin"
    assert security.decrypt_secret(token) == "password-garmin"


def test_password_hash_roundtrip():
    from app.auth import security

    h = security.hash_password("s3gret0!")
    assert security.verify_password("s3gret0!", h)
    assert not security.verify_password("sbagliata", h)


def test_verify_password_survives_malformed_hash():
    from app.auth import security

    assert security.verify_password("x", "non-un-hash") is False


# ============================================================================
# Sessioni
# ============================================================================

def test_session_token_roundtrip():
    from app.auth.session import create_session_token, read_session_token

    user = User(id=42, email="a@x.it", password_hash="h", session_epoch=3)
    assert read_session_token(create_session_token(user)) == (42, 3)


def test_tampered_session_token_rejected():
    from app.auth.session import create_session_token, read_session_token

    user = User(id=42, email="a@x.it", password_hash="h", session_epoch=0)
    assert read_session_token(create_session_token(user) + "x") is None
    assert read_session_token("spazzatura") is None


def test_a_token_without_the_epoch_is_refused():
    """I cookie della versione precedente non valgono più.

    Accettarli come «epoca 0» avrebbe lasciato in circolazione per trenta
    giorni proprio i token che l'epoca serve a poter revocare. Chi era
    collegato rifà l'accesso una volta.
    """
    from itsdangerous import URLSafeTimedSerializer

    from app.auth.session import read_session_token
    from app.config import settings

    vecchio = URLSafeTimedSerializer(
        settings.SESSION_SECRET, salt="gc-session"
    ).dumps({"uid": 42})
    assert read_session_token(vecchio) is None


# ============================================================================
# Registrazione
# ============================================================================

def test_registration_requires_an_invite(db):
    from app.auth import service

    with pytest.raises(service.InviteRequired):
        service.register(db, "a@x.it", PASSWORD)


def test_first_user_becomes_admin(db):
    from app.auth import service

    _invite(db, "INV1")
    user = service.register(db, "a@x.it", PASSWORD, display_name="Ada", invite_code="INV1")

    assert user.is_admin is True
    assert user.email == "a@x.it"
    assert user.display_name == "Ada"

    invite = db.query(InviteCode).one()
    assert invite.used_by_id == user.id and invite.used_at is not None


def test_the_second_user_is_not_admin(db):
    from app.auth import service

    _invite(db, "INV1")
    _invite(db, "INV2")
    service.register(db, "a@x.it", PASSWORD, invite_code="INV1")
    second = service.register(db, "b@x.it", PASSWORD, invite_code="INV2")

    assert second.is_admin is False


def test_a_missing_name_is_derived_from_the_email(db):
    from app.auth import service

    _invite(db, "INV1")
    user = service.register(db, "mario.rossi@x.it", PASSWORD, invite_code="INV1")

    assert user.display_name == "Mario Rossi"


def test_registration_has_no_data_source_yet(db):
    """Registrarsi e collegare i dati sono due momenti distinti."""
    from app.auth import service

    _invite(db, "INV1")
    user = service.register(db, "a@x.it", PASSWORD, invite_code="INV1")

    assert user.connection is None


def test_an_invite_cannot_be_reused(db):
    from app.auth import service

    _invite(db, "INV1")
    service.register(db, "a@x.it", PASSWORD, invite_code="INV1")

    with pytest.raises(service.InvalidInvite):
        service.register(db, "b@x.it", PASSWORD, invite_code="INV1")


def test_an_expired_invite_is_rejected(db):
    from app.auth import service

    _invite(db, "OLD", expires_at=datetime.utcnow() - timedelta(days=1))

    with pytest.raises(service.InvalidInvite):
        service.register(db, "a@x.it", PASSWORD, invite_code="OLD")


def test_the_same_email_cannot_register_twice(db):
    from app.auth import service

    _invite(db, "INV1")
    _invite(db, "INV2")
    service.register(db, "a@x.it", PASSWORD, invite_code="INV1")

    with pytest.raises(service.EmailTaken):
        service.register(db, "A@X.IT", PASSWORD, invite_code="INV2")


def test_a_short_password_is_refused_and_the_invite_stays_free(db):
    from app.auth import service

    _invite(db, "INV1")

    with pytest.raises(service.WeakPassword):
        service.register(db, "a@x.it", "corta", invite_code="INV1")

    assert db.query(User).count() == 0
    assert db.query(InviteCode).one().used_by_id is None


def test_the_email_is_normalised(db):
    from app.auth import service

    _invite(db, "INV1")
    user = service.register(db, "  Mario@Esempio.IT ", PASSWORD, invite_code="INV1")

    assert user.email == "mario@esempio.it"


# ============================================================================
# Accesso
# ============================================================================

def test_login_with_the_right_password(db):
    from app.auth import service

    _invite(db, "INV1")
    registered = service.register(db, "a@x.it", PASSWORD, invite_code="INV1")

    assert service.login(db, "a@x.it", PASSWORD).id == registered.id


def test_login_never_touches_a_provider(db, monkeypatch):
    """L'accesso all'app è locale: nessun fornitore viene contattato.

    Prima della v3 il login ricadeva su una verifica remota quando l'hash non
    corrispondeva. Se tornasse, un Garmin irraggiungibile — o un utente Strava,
    che un account Garmin non ce l'ha proprio — resterebbe fuori da casa sua.
    """
    from app.auth import service

    def explode(*args, **kwargs):
        raise AssertionError("il login non deve contattare nessun fornitore")

    monkeypatch.setattr("app.garmin.client.validate_credentials", explode)

    _invite(db, "INV1")
    service.register(db, "a@x.it", PASSWORD, invite_code="INV1")

    assert service.login(db, "a@x.it", PASSWORD)
    with pytest.raises(service.InvalidCredentials):
        service.login(db, "a@x.it", "un-altra-password")


def test_an_unknown_email_and_a_wrong_password_fail_the_same_way(db):
    """Distinguerli servirebbe solo a chi prova indirizzi a caso."""
    from app.auth import service

    _invite(db, "INV1")
    service.register(db, "a@x.it", PASSWORD, invite_code="INV1")

    with pytest.raises(service.InvalidCredentials):
        service.login(db, "sconosciuto@x.it", PASSWORD)
    with pytest.raises(service.InvalidCredentials):
        service.login(db, "a@x.it", "sbagliata")


def test_login_is_case_insensitive_on_the_email(db):
    from app.auth import service

    _invite(db, "INV1")
    service.register(db, "a@x.it", PASSWORD, invite_code="INV1")

    assert service.login(db, "A@X.IT", PASSWORD)


def test_a_disabled_account_is_rejected(db):
    from app.auth import service

    _invite(db, "INV1")
    user = service.register(db, "a@x.it", PASSWORD, invite_code="INV1")
    user.is_active = False
    db.commit()

    with pytest.raises(service.AccountDisabled):
        service.login(db, "a@x.it", PASSWORD)


# ============================================================================
# Password
# ============================================================================

def test_changing_the_password_requires_the_old_one(db):
    from app.auth import service

    _invite(db, "INV1")
    user = service.register(db, "a@x.it", PASSWORD, invite_code="INV1")

    with pytest.raises(service.InvalidCredentials):
        service.change_password(db, user, "non-e-questa", "nuova-password")

    service.change_password(db, user, PASSWORD, "nuova-password")
    assert service.login(db, "a@x.it", "nuova-password")


def test_a_new_password_must_be_long_enough(db):
    from app.auth import service

    _invite(db, "INV1")
    user = service.register(db, "a@x.it", PASSWORD, invite_code="INV1")

    with pytest.raises(service.WeakPassword):
        service.change_password(db, user, PASSWORD, "corta")


def test_the_admin_reset_does_not_need_the_old_password(db):
    """È il recupero password dell'app: non c'è email, c'è l'amministratore."""
    from app.auth import service

    _invite(db, "INV1")
    user = service.register(db, "a@x.it", PASSWORD, invite_code="INV1")

    service.set_password(db, user, "reimpostata-dall-admin")
    assert service.login(db, "a@x.it", "reimpostata-dall-admin")


# ============================================================================
# Modelli multi-utente
# ============================================================================

def test_same_day_allowed_for_different_users(db):
    u1 = User(email="a@x.it", password_hash="h")
    u2 = User(email="b@x.it", password_hash="h")
    db.add_all([u1, u2])
    db.flush()
    db.add_all([
        SleepRecord(user_id=u1.id, day=date(2026, 8, 1)),
        SleepRecord(user_id=u2.id, day=date(2026, 8, 1)),
    ])
    db.commit()
    assert db.query(SleepRecord).count() == 2
