"""Test dei moduli di autenticazione: crypto, sessioni, login/registrazione."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import InviteCode, SleepRecord, User


@pytest.fixture(autouse=True)
def fernet_key(monkeypatch):
    monkeypatch.setattr("app.config.settings.FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("app.config.settings.SESSION_SECRET", "test-secret")


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def ok_validator(email, password):
    return True


def ko_validator(email, password):
    return False


# --------------------------- security ---------------------------


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


# --------------------------- session ---------------------------


def test_session_token_roundtrip():
    from app.auth.session import create_session_token, read_session_token

    assert read_session_token(create_session_token(42)) == 42


def test_tampered_session_token_rejected():
    from app.auth.session import create_session_token, read_session_token

    assert read_session_token(create_session_token(42) + "x") is None
    assert read_session_token("spazzatura") is None


# --------------------------- auth service ---------------------------


def test_new_user_requires_invite(db):
    from app.auth import service

    with pytest.raises(service.InviteRequired):
        service.authenticate(db, "a@x.it", "pw", garmin_validator=ok_validator)


def test_registration_with_invite_makes_first_user_admin(db):
    from app.auth import service

    db.add(InviteCode(code="INV1"))
    db.commit()

    user = service.authenticate(
        db, "a@x.it", "pw", invite_code="INV1", garmin_validator=ok_validator
    )
    assert user.is_admin is True
    assert user.garmin_email == "a@x.it"

    invite = db.query(InviteCode).one()
    assert invite.used_by_id == user.id and invite.used_at is not None


def test_invite_cannot_be_reused(db):
    from app.auth import service

    db.add(InviteCode(code="INV1"))
    db.commit()
    service.authenticate(db, "a@x.it", "pw", invite_code="INV1", garmin_validator=ok_validator)

    with pytest.raises(service.InvalidInvite):
        service.authenticate(
            db, "b@x.it", "pw", invite_code="INV1", garmin_validator=ok_validator
        )


def test_expired_invite_rejected(db):
    from app.auth import service

    db.add(InviteCode(code="OLD", expires_at=datetime.utcnow() - timedelta(days=1)))
    db.commit()

    with pytest.raises(service.InvalidInvite):
        service.authenticate(
            db, "a@x.it", "pw", invite_code="OLD", garmin_validator=ok_validator
        )


def test_registration_rejected_if_garmin_refuses(db):
    from app.auth import service

    db.add(InviteCode(code="INV2"))
    db.commit()

    with pytest.raises(service.InvalidCredentials):
        service.authenticate(
            db, "a@x.it", "pw", invite_code="INV2", garmin_validator=ko_validator
        )
    # nessun utente creato, invito ancora libero
    assert db.query(User).count() == 0
    assert db.query(InviteCode).one().used_by_id is None


def test_login_does_not_call_garmin(db):
    """Il login normale è locale: Garmin non viene contattato."""
    from app.auth import service

    db.add(InviteCode(code="INV3"))
    db.commit()
    service.authenticate(db, "a@x.it", "pw", invite_code="INV3", garmin_validator=ok_validator)

    def exploding_validator(email, password):
        raise AssertionError("Garmin non deve essere contattato al login normale")

    assert service.authenticate(db, "a@x.it", "pw", garmin_validator=exploding_validator)


def test_password_self_healing_when_changed_on_garmin(db):
    from app.auth import service
    from app.auth.security import verify_password

    db.add(InviteCode(code="INV4"))
    db.commit()
    service.authenticate(
        db, "a@x.it", "vecchia", invite_code="INV4", garmin_validator=ok_validator
    )

    user = service.authenticate(db, "a@x.it", "nuova", garmin_validator=ok_validator)
    assert verify_password("nuova", user.garmin_password_hash)

    from app.auth.security import decrypt_secret

    assert decrypt_secret(user.garmin_password_encrypted) == "nuova"


def test_wrong_password_rejected_everywhere(db):
    from app.auth import service

    db.add(InviteCode(code="INV5"))
    db.commit()
    service.authenticate(db, "a@x.it", "pw", invite_code="INV5", garmin_validator=ok_validator)

    with pytest.raises(service.InvalidCredentials):
        service.authenticate(db, "a@x.it", "sbagliata", garmin_validator=ko_validator)


def test_disabled_account_rejected(db):
    from app.auth import service

    db.add(InviteCode(code="INV6"))
    db.commit()
    user = service.authenticate(
        db, "a@x.it", "pw", invite_code="INV6", garmin_validator=ok_validator
    )
    user.is_active = False
    db.commit()

    with pytest.raises(service.AccountDisabled):
        service.authenticate(db, "a@x.it", "pw", garmin_validator=ok_validator)


# --------------------------- modelli multi-utente ---------------------------


def test_same_day_allowed_for_different_users(db):
    u1 = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    u2 = User(garmin_email="b@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add_all([u1, u2])
    db.flush()
    db.add_all(
        [
            SleepRecord(user_id=u1.id, day=__import__("datetime").date(2026, 8, 1)),
            SleepRecord(user_id=u2.id, day=__import__("datetime").date(2026, 8, 1)),
        ]
    )
    db.commit()
    assert db.query(SleepRecord).count() == 2
