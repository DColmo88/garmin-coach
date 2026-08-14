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


def test_create_invite_persists_code(cli, test_db):
    from app.db.models import InviteCode

    code = cli.create_invite()
    session = test_db()
    invite = session.query(InviteCode).filter_by(code=code).one()
    assert invite.used_by_id is None and invite.expires_at is None
    session.close()


def test_create_invite_with_expiry(cli, test_db):
    from app.db.models import InviteCode

    code = cli.create_invite(expires_days=7)
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
    session.add(User(garmin_email="a@x.it", garmin_password_encrypted="e",
                     garmin_password_hash="h", is_admin=True))
    session.commit()
    session.close()

    rows = cli.list_users()
    assert len(rows) == 1
    assert "a@x.it" in rows[0] and "admin" in rows[0]


def test_set_admin(cli, test_db):
    from app.db.models import User

    session = test_db()
    session.add(User(garmin_email="a@x.it", garmin_password_encrypted="e",
                     garmin_password_hash="h"))
    session.commit()
    session.close()

    assert cli.set_admin("a@x.it") is True
    assert cli.set_admin("inesistente@x.it") is False

    session = test_db()
    assert session.query(User).one().is_admin is True
    session.close()
