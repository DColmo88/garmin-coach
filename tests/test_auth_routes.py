"""Test end-to-end dell'accesso: protezione pagine, login, registrazione, logout."""
from __future__ import annotations

import pytest

PROTECTED = ["/", "/coach", "/sleep", "/health", "/body", "/performance", "/activities"]


@pytest.mark.parametrize("path", PROTECTED)
def test_pages_redirect_to_login_when_anonymous(client, path):
    response = client.get(path)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_sync_endpoint_is_protected(client):
    assert client.post("/sync").status_code == 303


def test_api_context_is_protected(client):
    assert client.get("/api/context").status_code == 303


def test_healthz_is_public(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Garmin" in response.text


def test_first_login_without_invite_asks_for_one(client):
    response = client.post("/login", data={"email": "a@x.it", "password": "pw"})
    assert response.status_code == 200
    assert "codice invito" in response.text.lower()
    assert 'name="invite_code"' in response.text


def test_login_with_invite_creates_session(logged_client):
    response = logged_client.get("/coach")
    assert response.status_code == 200


def test_wrong_credentials_show_error(client, test_db):
    from app.db.models import InviteCode

    session = test_db()
    session.add(InviteCode(code="INV"))
    session.commit()
    session.close()

    response = client.post(
        "/login", data={"email": "x@x.it", "password": "wrong", "invite_code": "INV"}
    )
    assert response.status_code == 200
    assert "non valide" in response.text.lower()


def test_bad_invite_shows_error(client):
    response = client.post(
        "/login", data={"email": "x@x.it", "password": "pw", "invite_code": "INESISTENTE"}
    )
    assert response.status_code == 200
    assert "invito" in response.text.lower()


def test_logout_clears_session(logged_client):
    response = logged_client.post("/logout")
    assert response.status_code == 303
    assert logged_client.get("/coach").status_code == 303


def test_login_page_redirects_when_already_logged_in(logged_client):
    response = logged_client.get("/login")
    assert response.status_code == 303
    assert response.headers["location"] == "/coach"


def test_second_user_is_not_admin(client, test_db):
    from sqlalchemy import select

    from app.db.models import InviteCode, User

    session = test_db()
    session.add_all([InviteCode(code="A"), InviteCode(code="B")])
    session.commit()
    session.close()

    client.post("/login", data={"email": "primo@x.it", "password": "pw", "invite_code": "A"})
    client.post("/login", data={"email": "secondo@x.it", "password": "pw", "invite_code": "B"})

    session = test_db()
    users = {u.garmin_email: u.is_admin for u in session.scalars(select(User)).all()}
    session.close()
    assert users == {"primo@x.it": True, "secondo@x.it": False}
