"""Pagina di amministrazione: accesso, inviti, quote, disattivazione."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.db.models import AIUsageLog, InviteCode, User


def admin_of(test_db) -> User:
    """L'utente creato da `logged_client` è il primo, quindi admin."""
    session = test_db()
    try:
        return session.scalar(select(User).where(User.email == "test@x.it"))
    finally:
        session.close()


@pytest.fixture()
def second_user(test_db) -> User:
    session = test_db()
    user = User(email="secondo@x.it", password_hash="h")
    session.add(user)
    session.commit()
    user_id = user.id
    session.close()

    session = test_db()
    try:
        return session.get(User, user_id)
    finally:
        session.close()


# ============================================================================
# Accesso
# ============================================================================

def test_admin_requires_login(client):
    assert client.get("/admin").status_code == 303


def test_first_user_is_admin_and_can_open_the_page(logged_client, test_db):
    assert admin_of(test_db).is_admin is True
    assert logged_client.get("/admin").status_code == 200


def test_non_admin_is_sent_away(logged_client, test_db):
    session = test_db()
    user = session.scalar(select(User).where(User.email == "test@x.it"))
    user.is_admin = False
    session.commit()
    session.close()

    response = logged_client.get("/admin")
    assert response.status_code == 303
    assert response.headers["location"] == "/coach"


def test_admin_link_only_shows_for_admins(logged_client, test_db):
    assert 'href="/admin"' in logged_client.get("/coach").text

    session = test_db()
    user = session.scalar(select(User).where(User.email == "test@x.it"))
    user.is_admin = False
    session.commit()
    session.close()

    assert 'href="/admin"' not in logged_client.get("/coach").text


def test_non_admin_cannot_use_the_actions(logged_client, test_db, second_user):
    session = test_db()
    user = session.scalar(select(User).where(User.email == "test@x.it"))
    user.is_admin = False
    session.commit()
    before = session.query(InviteCode).count()
    session.close()

    assert logged_client.post("/admin/invite", data={"expires_days": 30}).status_code == 303

    session = test_db()
    assert session.query(InviteCode).count() == before  # nessun invito creato
    session.close()


# ============================================================================
# Contenuto
# ============================================================================

def test_page_lists_users(logged_client, second_user):
    page = logged_client.get("/admin").text
    assert "test@x.it" in page and "secondo@x.it" in page


def test_page_shows_ai_spend(logged_client, test_db):
    session = test_db()
    user = session.scalar(select(User).where(User.email == "test@x.it"))
    session.add(AIUsageLog(user_id=user.id, day=date.today(), kind="chat",
                           model="claude-haiku-4-5",
                           tokens_in=1_000_000, tokens_out=200_000))
    session.commit()
    session.close()

    page = logged_client.get("/admin").text
    assert "Spesa AI" in page
    assert "$2.00" in page  # 1.00 in + 1.00 out (200k × 5$/M)


def test_page_survives_an_empty_database(logged_client):
    assert logged_client.get("/admin").status_code == 200


# ============================================================================
# Inviti
# ============================================================================

def test_create_invite(logged_client, test_db):
    response = logged_client.post("/admin/invite", data={"expires_days": 30})
    assert response.status_code == 303
    code = response.headers["location"].split("created=")[1]

    session = test_db()
    invite = session.query(InviteCode).filter_by(code=code).one()
    assert invite.expires_at is not None
    assert invite.created_by_id is not None
    session.close()


def test_create_invite_without_expiry(logged_client, test_db):
    response = logged_client.post("/admin/invite", data={"expires_days": 0})
    code = response.headers["location"].split("created=")[1]

    session = test_db()
    assert session.query(InviteCode).filter_by(code=code).one().expires_at is None
    session.close()


def test_the_banner_shows_a_link_not_a_code(logged_client):
    response = logged_client.post("/admin/invite", data={"expires_days": 7})
    code = response.headers["location"].split("created=")[1]

    with_banner = logged_client.get(f"/admin?created={code}").text
    assert "Invito creato" in with_banner
    assert f"/register?invite={code}" in with_banner
    assert "copy-link" in with_banner

    # Ricaricando senza il parametro il banner sparisce, ma il link resta
    # nell'elenco degli inviti: e' li' che si va a ripescarlo.
    plain = logged_client.get("/admin").text
    assert "Invito creato" not in plain
    assert f"/register?invite={code}" in plain


def test_a_spent_invite_is_no_longer_a_link(logged_client, test_db):
    """Un invito usato è una riga di storia, non qualcosa da mandare."""
    from app.db.models import InviteCode

    response = logged_client.post("/admin/invite", data={"expires_days": 7})
    code = response.headers["location"].split("created=")[1]

    session = test_db()
    invite = session.query(InviteCode).filter_by(code=code).one()
    invite.used_by_id = session.scalar(select(User.id))
    session.commit()
    session.close()

    body = logged_client.get("/admin").text
    assert f"/register?invite={code}" not in body
    assert code in body  # resta leggibile nell'elenco


def test_the_link_leads_to_a_prefilled_form(logged_client):
    """Il punto di tutto: chi clicca trova il campo già riempito."""
    response = logged_client.post("/admin/invite", data={"expires_days": 7})
    code = response.headers["location"].split("created=")[1]

    # Chi riceve l'invito non è autenticato: `logged_client` e `client` sono lo
    # stesso oggetto, quindi senza uscire si verrebbe rimandati al coach.
    logged_client.post("/logout")

    page = logged_client.get(f"/register?invite={code}").text
    assert f'value="{code}"' in page
    assert 'name="invite_code"' in page


def test_invite_created_here_actually_works(logged_client, client, test_db):
    """Il giro completo: creo l'invito, un nuovo utente lo usa."""
    response = logged_client.post("/admin/invite", data={"expires_days": 30})
    code = response.headers["location"].split("created=")[1]

    logged_client.post("/logout")
    registration = logged_client.post("/register", data={
        "email": "nuovo@x.it", "password": "password-di-prova",
        "password_confirm": "password-di-prova", "invite_code": code,
    })
    assert registration.status_code == 303

    session = test_db()
    assert session.query(User).filter_by(email="nuovo@x.it").count() == 1
    session.close()


# ============================================================================
# Quote e stato degli utenti
# ============================================================================

def test_set_quota(logged_client, test_db, second_user):
    logged_client.post(f"/admin/users/{second_user.id}/quota",
                       data={"chat_daily": 5, "plans_monthly": 1})

    session = test_db()
    user = session.get(User, second_user.id)
    assert user.ai_quota_chat_daily == 5 and user.ai_quota_plans_monthly == 1
    session.close()


def test_negative_quota_becomes_zero(logged_client, test_db, second_user):
    logged_client.post(f"/admin/users/{second_user.id}/quota",
                       data={"chat_daily": -10, "plans_monthly": -3})

    session = test_db()
    user = session.get(User, second_user.id)
    assert user.ai_quota_chat_daily == 0 and user.ai_quota_plans_monthly == 0
    session.close()


def test_toggle_disables_and_reenables(logged_client, test_db, second_user):
    logged_client.post(f"/admin/users/{second_user.id}/toggle")
    session = test_db()
    assert session.get(User, second_user.id).is_active is False
    session.close()

    logged_client.post(f"/admin/users/{second_user.id}/toggle")
    session = test_db()
    assert session.get(User, second_user.id).is_active is True
    session.close()


def test_admin_cannot_lock_themselves_out(logged_client, test_db):
    admin = admin_of(test_db)
    logged_client.post(f"/admin/users/{admin.id}/toggle")
    assert admin_of(test_db).is_active is True


def test_disabled_user_cannot_log_in(logged_client, client, test_db, second_user):
    """La disattivazione deve avere effetto sul login, non solo sulla lista."""
    from app.auth import service

    logged_client.post(f"/admin/users/{second_user.id}/toggle")

    session = test_db()
    user = session.get(User, second_user.id)
    service.set_password(session, user, "password-di-prova")
    with pytest.raises(service.AccountDisabled):
        service.login(session, "secondo@x.it", "password-di-prova")
    session.close()


def test_toggle_of_a_missing_user_does_not_crash(logged_client):
    assert logged_client.post("/admin/users/9999/toggle").status_code == 303


# ============================================================================
# Reset della password: il recupero dell'app, al posto dell'email
# ============================================================================

def test_the_admin_can_reset_a_password(logged_client, test_db, second_user):
    from app.auth import service

    response = logged_client.post(f"/admin/users/{second_user.id}/password",
                                  data={"new_password": "reimpostata-a-mano"})

    assert response.status_code == 303
    session = test_db()
    try:
        assert service.login(session, "secondo@x.it", "reimpostata-a-mano")
    finally:
        session.close()


def test_a_short_reset_is_refused(logged_client, test_db, second_user):
    from urllib.parse import unquote

    response = logged_client.post(f"/admin/users/{second_user.id}/password",
                                  data={"new_password": "corta"})

    assert "almeno" in unquote(response.headers["location"])


def test_resetting_a_missing_user_does_not_crash(logged_client):
    response = logged_client.post("/admin/users/9999/password",
                                  data={"new_password": "qualunque-cosa"})
    assert response.status_code == 303


def test_only_an_admin_can_reset(logged_client, test_db, second_user):
    session = test_db()
    me = session.scalar(select(User).where(User.email == "test@x.it"))
    me.is_admin = False
    session.commit()
    session.close()

    response = logged_client.post(f"/admin/users/{second_user.id}/password",
                                  data={"new_password": "provo-lo-stesso"})

    assert response.status_code == 303
    assert response.headers["location"] == "/coach"
