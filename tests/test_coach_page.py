# tests/test_coach_page.py
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_coach_page_renders_200():
    resp = client.get("/coach")
    assert resp.status_code == 200
    assert "READINESS" in resp.text or "Prontezza" in resp.text


def test_coach_page_has_nav_link():
    resp = client.get("/coach")
    assert 'href="/coach"' in resp.text
