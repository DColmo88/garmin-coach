"""End-to-end dell'accesso: protezione pagine, login, registrazione, logout.

Dalla v3 login e registrazione sono **due pagine diverse**. Prima coincidevano
perché le credenziali Garmin facevano da entrambe le cose; adesso `/login`
verifica un account che esiste e `/register` ne crea uno.
"""
from __future__ import annotations

import pytest

PROTECTED = ["/", "/coach", "/sleep", "/health", "/body", "/fitness", "/activities"]


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


def test_the_login_page_points_to_registration(client):
    """Chi non ha un account deve trovare la strada, non indovinarla."""
    response = client.get("/login")
    assert '/register' in response.text


def test_the_registration_page_asks_for_an_invite(client):
    response = client.get("/register")
    assert response.status_code == 200
    assert "codice invito" in response.text.lower()
    assert 'name="invite_code"' in response.text


def test_registration_without_an_invite_is_refused(client):
    response = client.post("/register", data={
        "email": "a@x.it", "password": "password-di-prova",
        "password_confirm": "password-di-prova",
    })
    assert response.status_code == 200
    assert "invito" in response.text.lower()


def test_the_two_passwords_must_match(client, test_db):
    from app.db.models import InviteCode

    session = test_db()
    session.add(InviteCode(code="INV"))
    session.commit()
    session.close()

    response = client.post("/register", data={
        "email": "a@x.it", "password": "password-di-prova",
        "password_confirm": "un-altra-password", "invite_code": "INV",
    })
    assert response.status_code == 200
    assert "non coincidono" in response.text.lower()


def test_a_short_password_is_refused(client, test_db):
    from app.db.models import InviteCode

    session = test_db()
    session.add(InviteCode(code="INV"))
    session.commit()
    session.close()

    response = client.post("/register", data={
        "email": "a@x.it", "password": "corta", "password_confirm": "corta",
        "invite_code": "INV",
    })
    assert response.status_code == 200
    assert "almeno" in response.text.lower()


def test_registration_leads_to_connecting_a_source(client, test_db):
    """Il secondo passo non si scopre da soli: ci si finisce dentro."""
    from app.db.models import InviteCode

    session = test_db()
    session.add(InviteCode(code="INV"))
    session.commit()
    session.close()

    response = client.post("/register", data={
        "email": "a@x.it", "password": "password-di-prova",
        "password_confirm": "password-di-prova", "invite_code": "INV",
    })
    assert response.status_code == 303
    assert response.headers["location"] == "/connect"


def test_registration_creates_a_session(logged_client):
    response = logged_client.get("/coach")
    assert response.status_code == 200


def test_wrong_credentials_show_an_error(client):
    response = client.post("/login", data={"email": "x@x.it", "password": "sbagliata"})
    assert response.status_code == 200
    assert "non corretti" in response.text.lower()


def test_a_bad_invite_shows_an_error(client):
    response = client.post("/register", data={
        "email": "x@x.it", "password": "password-di-prova",
        "password_confirm": "password-di-prova", "invite_code": "INESISTENTE",
    })
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

    for email, code in (("primo@x.it", "A"), ("secondo@x.it", "B")):
        client.post("/register", data={
            "email": email, "password": "password-di-prova",
            "password_confirm": "password-di-prova", "invite_code": code,
        })
        client.post("/logout")

    session = test_db()
    users = {u.email: u.is_admin for u in session.scalars(select(User)).all()}
    session.close()
    assert users == {"primo@x.it": True, "secondo@x.it": False}
