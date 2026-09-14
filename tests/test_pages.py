"""Tutte le pagine devono rispondere 200 anche senza dati (nessun crash)."""
from __future__ import annotations

import pytest

PAGES = ["/", "/coach", "/activities", "/sleep", "/health", "/body", "/fitness"]


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


# ============================================================================
# Rotte ritirate
# ============================================================================

@pytest.mark.parametrize("old,new", [
    ("/performance", "/fitness"),  # confluita in Forma e carico
    ("/ai", "/chat"),              # banco di prova della v1, con un bottone morto
])
def test_retired_routes_point_at_their_replacement(logged_client, old, new):
    """Un segnalibro vecchio deve arrivare da qualche parte, non su un 404."""
    response = logged_client.get(old)
    assert response.status_code == 301
    assert response.headers["location"] == new


def test_pages_show_logged_user(logged_client):
    assert "test@x.it" in logged_client.get("/coach").text or "Test" in logged_client.get("/coach").text


def test_activity_detail_of_unknown_activity_does_not_crash(logged_client, monkeypatch):
    from app.garmin import service

    monkeypatch.setattr(service, "get_activity_full", lambda user, aid: {})
    assert logged_client.get("/activities/999").status_code == 200


# --------------------------- cache degli asset ---------------------------

def test_static_version_follows_file_changes(tmp_path, monkeypatch):
    """La versione va ricalcolata, non congelata all'import.

    Se resta ferma, dopo una modifica al CSS o al JS i browser continuano a
    servire il file vecchio dalla cache.
    """
    import time

    from app import templating

    monkeypatch.setattr(templating, "STATIC_DIR", tmp_path)
    asset = tmp_path / "style.css"
    asset.write_text("a{}")
    first = templating.static_version()

    time.sleep(1.1)  # la versione ha risoluzione al secondo
    asset.write_text("a{color:red}")
    assert templating.static_version() != first


def test_static_version_is_a_callable_in_templates():
    """I template chiamano static_v(): se fosse un valore, non si aggiornerebbe."""
    from app.templating import templates

    assert callable(templates.env.globals["static_v"])


def test_pages_carry_a_cache_busted_asset_url(logged_client):
    page = logged_client.get("/coach").text
    assert "/static/style.css?v=" in page
    assert "/static/app.js?v=" in page
    assert "{{" not in page.split("</head>")[0]  # nessuna espressione non renderizzata


def test_static_version_survives_a_missing_directory(tmp_path, monkeypatch):
    from app import templating

    monkeypatch.setattr(templating, "STATIC_DIR", tmp_path / "inesistente")
    assert templating.static_version() == "0"
