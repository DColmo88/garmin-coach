# tests/test_coach_page.py
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.database import Base, get_session
from app.main import app

client = TestClient(app)


def test_coach_page_renders_200():
    resp = client.get("/coach")
    assert resp.status_code == 200
    # Marker sempre presente, indipendente dai dati nel DB locale.
    assert 'class="coach-hero"' in resp.text


def test_coach_page_has_nav_link():
    resp = client.get("/coach")
    assert 'href="/coach"' in resp.text


def test_coach_page_empty_db_still_200():
    """Su DB vuoto la pagina non deve crashare: 200 + stato 'nessun dato'."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    def _empty_session():
        db = Session(engine)
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_session] = _empty_session
    try:
        resp = client.get("/coach")
        assert resp.status_code == 200
        assert 'class="coach-hero"' in resp.text
        # readiness.score None → messaggio coach invita a sincronizzare
        assert "Sincronizza" in resp.text
    finally:
        app.dependency_overrides.pop(get_session, None)
