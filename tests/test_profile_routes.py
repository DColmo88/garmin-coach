"""Le soglie fisiologiche dalla pagina impostazioni.

Un errore di battitura qui falsa ogni carico calcolato da qui in avanti: la
validazione è la parte che conta.
"""
from __future__ import annotations

from sqlalchemy import select

from app.db.models import User


def _reload(test_db, email: str = "test@x.it") -> User:
    session = test_db()
    try:
        return session.scalar(select(User).where(User.email == email))
    finally:
        session.close()


def _post(client, **fields):
    data = {"hr_max": "", "hr_rest": "", "lthr": "", "ftp": "",
            "birth_year": "", "sex": ""}
    data.update({k: str(v) for k, v in fields.items()})
    return client.post("/settings/profile", data=data)


def test_the_page_shows_the_profile_section(logged_client):
    body = logged_client.get("/settings").text
    assert "Le tue soglie" in body
    assert 'name="hr_max"' in body


def test_declared_thresholds_are_saved(logged_client, test_db):
    response = _post(logged_client, hr_max=194, hr_rest=46, lthr=170, ftp=255,
                     birth_year=1988, sex="m")
    assert response.status_code == 303

    user = _reload(test_db)
    assert (user.hr_max, user.hr_rest, user.lthr, user.ftp) == (194, 46, 170, 255)
    assert (user.birth_year, user.sex) == (1988, "m")


def test_an_empty_field_goes_back_to_the_estimate(logged_client, test_db):
    _post(logged_client, hr_max=194)
    assert _reload(test_db).hr_max == 194

    _post(logged_client)  # tutti i campi vuoti
    assert _reload(test_db).hr_max is None


def test_an_impossible_value_is_refused(logged_client, test_db):
    """Una FC massima di 400 è un errore di battitura, non una misura."""
    _post(logged_client, hr_max=400, hr_rest=2)

    user = _reload(test_db)
    assert user.hr_max is None
    assert user.hr_rest is None


def test_text_in_a_numeric_field_does_not_crash(logged_client, test_db):
    response = _post(logged_client, hr_max="centonovanta")
    assert response.status_code == 303
    assert _reload(test_db).hr_max is None


def test_an_invented_sex_is_discarded(logged_client, test_db):
    _post(logged_client, sex="qualcosa")
    assert _reload(test_db).sex is None


def test_the_thresholds_reach_the_fitness_page(logged_client, monkeypatch):
    from app.garmin import service

    monkeypatch.setattr(service, "get_performance_snapshot", lambda user: None)

    _post(logged_client, hr_max=201)
    body = logged_client.get("/fitness").text

    assert "201" in body
    assert "dichiarata" in body


def test_the_profile_route_is_protected(client):
    assert client.post("/settings/profile", data={}).status_code == 303
