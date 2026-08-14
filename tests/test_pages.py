"""Tutte le pagine devono rispondere 200 anche senza dati (nessun crash)."""
from __future__ import annotations

import pytest

PAGES = ["/", "/coach", "/activities", "/sleep", "/health", "/body", "/performance"]


@pytest.fixture(autouse=True)
def no_live_garmin(monkeypatch):
    """Le pagine che leggono dati live non devono contattare la rete nei test."""
    from app.garmin import service

    monkeypatch.setattr(service, "get_performance_snapshot", lambda user: None)
    monkeypatch.setattr(service, "get_devices", lambda user: [])
    monkeypatch.setattr(service, "get_gear_overview", lambda user: [])
    monkeypatch.setattr(service, "get_badges", lambda user: {})


@pytest.mark.parametrize("path", PAGES)
def test_page_renders_on_empty_db(logged_client, path):
    response = logged_client.get(path)
    assert response.status_code == 200, response.text


def test_devices_page_renders(logged_client):
    assert logged_client.get("/devices").status_code == 200


def test_pages_show_logged_user(logged_client):
    assert "test@x.it" in logged_client.get("/coach").text or "Test" in logged_client.get("/coach").text


def test_activity_detail_of_unknown_activity_does_not_crash(logged_client, monkeypatch):
    from app.garmin import service

    monkeypatch.setattr(service, "get_activity_full", lambda user, aid: {})
    assert logged_client.get("/activities/999").status_code == 200
