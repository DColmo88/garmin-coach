"""CLI amministrativa: inviti e utenti."""
from __future__ import annotations

from datetime import datetime

import pytest


@pytest.fixture()
def cli(monkeypatch, test_db):
    """La CLI lavora sul DB di test invece che su quello reale."""
    import app.cli as cli_module

    monkeypatch.setattr(cli_module, "SessionLocal", test_db)
    monkeypatch.setattr(cli_module, "init_db", lambda: None)
    return cli_module


def test_create_invite_returns_a_ready_to_send_link(cli, test_db):
    """Non il codice nudo: chi lo riceve dovrebbe capire dove incollarlo."""
    from app.db.models import InviteCode

    link = cli.create_invite()

    assert "/register?invite=" in link
    code = link.split("invite=")[1]

    session = test_db()
    invite = session.query(InviteCode).filter_by(code=code).one()
    assert invite.used_by_id is None and invite.expires_at is None
    session.close()


def test_create_invite_with_expiry(cli, test_db):
    from app.db.models import InviteCode

    code = cli.create_invite(expires_days=7).split("invite=")[1]
    session = test_db()
    invite = session.query(InviteCode).filter_by(code=code).one()
    assert invite.expires_at is not None and invite.expires_at > datetime.utcnow()
    session.close()


def test_invite_codes_are_unique(cli):
    assert cli.create_invite() != cli.create_invite()


def test_list_users_empty(cli):
    assert cli.list_users() == []


def test_list_users_shows_registered(cli, test_db):
    from app.db.models import User

    session = test_db()
    session.add(User(email="a@x.it", password_hash="h", is_admin=True))
    session.commit()
    session.close()

    rows = cli.list_users()
    assert len(rows) == 1
    assert "a@x.it" in rows[0] and "admin" in rows[0]


def test_set_admin(cli, test_db):
    from app.db.models import User

    session = test_db()
    session.add(User(email="a@x.it", password_hash="h"))
    session.commit()
    session.close()

    assert cli.set_admin("a@x.it") is True
    assert cli.set_admin("inesistente@x.it") is False

    session = test_db()
    assert session.query(User).one().is_admin is True
    session.close()


# --------------------------- notifiche ---------------------------

def test_vapid_keys_are_usable_by_pywebpush():
    """Le chiavi generate devono firmare davvero, non solo sembrare corrette."""
    import re

    from py_vapid import Vapid01

    from app.cli import generate_vapid_keys

    output = generate_vapid_keys()
    public = re.search(r"VAPID_PUBLIC_KEY=(\S+)", output).group(1)
    private = re.search(r'VAPID_PRIVATE_KEY="(.+)"', output).group(1).replace("\\n", "\n")

    # la pubblica è un punto non compresso P-256 (65 byte) in base64url
    assert len(public) == 87 and "=" not in public

    headers = Vapid01.from_pem(private.encode()).sign(
        {"aud": "https://push.example.com", "sub": "mailto:a@b.it"}
    )
    assert "Authorization" in headers


def test_vapid_keys_differ_each_time():
    from app.cli import generate_vapid_keys

    assert generate_vapid_keys() != generate_vapid_keys()


def test_telegram_webhook_url_needs_a_token(monkeypatch):
    from app.cli import telegram_webhook_url

    monkeypatch.setattr("app.config.settings.TELEGRAM_BOT_TOKEN", "")
    assert "non impostato" in telegram_webhook_url()

    monkeypatch.setattr("app.config.settings.TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setattr("app.config.settings.PUBLIC_BASE_URL", "https://coach.example.it")
    output = telegram_webhook_url()
    assert "https://coach.example.it/telegram/webhook/" in output
    assert "setWebhook" in output
