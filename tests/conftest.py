"""Fixture condivise.

L'app di test gira su un SQLite temporaneo e con un Garmin finto: nessun test
tocca mai la rete o il database reale.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def no_background_scheduler(monkeypatch):
    """Lo scheduler reale non deve mai partire durante i test."""
    monkeypatch.setattr("app.config.settings.SCHEDULER_ENABLED", False)


@pytest.fixture()
def test_db(monkeypatch, tmp_path):
    """Engine + sessionmaker su un DB temporaneo, con le tabelle già create."""
    monkeypatch.setattr("app.config.settings.FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("app.config.settings.SESSION_SECRET", "test-secret")
    monkeypatch.setattr("app.config.settings.GARMIN_TOKENSTORE", str(tmp_path / "tokens"))

    from app.db.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}", connect_args={"check_same_thread": False}
    )
    import app.db.models  # noqa: F401 — registra i modelli su Base

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    # Il codice che apre una sessione per conto suo (scheduler, streaming SSE)
    # non passa dalla dependency di FastAPI. Senza questo patch scriverebbe sul
    # database reale durante i test.
    monkeypatch.setattr("app.db.database.SessionLocal", factory)
    monkeypatch.setattr("app.routers.chat.SessionLocal", factory)
    monkeypatch.setattr("app.scheduler.SessionLocal", factory)

    return factory


@pytest.fixture()
def db(test_db):
    """Sessione diretta sul DB di test (per i test che non passano dall'HTTP)."""
    session = test_db()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(test_db, monkeypatch):
    """TestClient con la dependency del DB sostituita e Garmin finto.

    Le credenziali sono accettate a meno che la password sia "wrong": così i
    test possono simulare sia il login riuscito sia quello rifiutato.
    """
    monkeypatch.setattr(
        "app.auth.service._default_validator", lambda email, password: password != "wrong"
    )

    from app.db.database import get_session
    from app.main import app

    def override_get_session():
        session = test_db()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    with TestClient(app, follow_redirects=False) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def logged_client(client, test_db):
    """Client con una sessione attiva (utente registrato tramite invito)."""
    from app.db.models import InviteCode

    session = test_db()
    session.add(InviteCode(code="TEST-INVITE"))
    session.commit()
    session.close()

    response = client.post(
        "/login",
        data={"email": "test@x.it", "password": "pw", "invite_code": "TEST-INVITE"},
    )
    assert response.status_code == 303, response.text
    return client


@pytest.fixture()
def logged_user(logged_client, test_db):
    """L'utente corrispondente a `logged_client`."""
    from sqlalchemy import select

    from app.db.models import User

    session = test_db()
    try:
        return session.scalar(select(User).where(User.garmin_email == "test@x.it"))
    finally:
        session.close()
