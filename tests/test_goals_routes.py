"""La pagina obiettivi: rendering, salvataggio, archiviazione."""
from __future__ import annotations

from datetime import date

import pytest


def test_goals_page_requires_login(client):
    assert client.get("/goals").status_code == 303


def test_goals_page_renders_empty_state(logged_client):
    response = logged_client.get("/goals")
    assert response.status_code == 200
    assert "Non hai ancora un obiettivo" in response.text
    # tutte le schede tipo sono presenti
    assert "Gara 10K" in response.text and "Peso forma" in response.text


def test_save_goal_and_see_it(logged_client):
    response = logged_client.post("/goals", data={
        "goal_type": "race_10k",
        "title": "",
        "target_date": "2026-10-04",
        "p_target_time": "50:00",
        "p_weekly_km": "35",
        "priority_notes": "Ginocchio delicato",
    })
    assert response.status_code == 303
    assert response.headers["location"] == "/goals?saved=1"

    page = logged_client.get("/goals?saved=1").text
    assert "Obiettivo attivo" in page
    assert "Gara 10K" in page
    assert "50:00" in page
    assert "Ginocchio delicato" in page


def test_saved_goal_reaches_the_database(logged_client, test_db):
    logged_client.post("/goals", data={
        "goal_type": "weight",
        "p_target_weight": "72",
        "p_weekly_rate": "0,4",
    })
    from app.db.models import UserGoal

    session = test_db()
    goal = session.query(UserGoal).filter_by(active=True).one()
    assert goal.goal_type == "weight"
    assert goal.params_json == {"target_weight": 72.0, "weekly_rate": 0.4}
    session.close()


def test_changing_goal_archives_the_previous(logged_client, test_db):
    logged_client.post("/goals", data={"goal_type": "race_5k"})
    logged_client.post("/goals", data={"goal_type": "race_marathon"})

    from app.db.models import UserGoal

    session = test_db()
    active = session.query(UserGoal).filter_by(active=True).all()
    archived = session.query(UserGoal).filter_by(active=False).all()
    assert len(active) == 1 and active[0].goal_type == "race_marathon"
    assert len(archived) == 1 and archived[0].goal_type == "race_5k"
    session.close()

    assert "Obiettivi passati" in logged_client.get("/goals").text


def test_close_goal(logged_client):
    logged_client.post("/goals", data={"goal_type": "race_5k"})
    response = logged_client.post("/goals/close", data={"outcome": "Fatta in 24:31"})
    assert response.status_code == 303

    page = logged_client.get("/goals").text
    assert "Non hai ancora un obiettivo" in page
    assert "Fatta in 24:31" in page


def test_invalid_goal_type_does_not_crash(logged_client, test_db):
    response = logged_client.post("/goals", data={"goal_type": "inventato"})
    assert response.status_code == 303

    from app.db.models import UserGoal

    session = test_db()
    assert session.query(UserGoal).count() == 0
    session.close()


def test_malformed_date_is_ignored(logged_client, test_db):
    response = logged_client.post(
        "/goals", data={"goal_type": "race_5k", "target_date": "non-una-data"}
    )
    assert response.status_code == 303

    from app.db.models import UserGoal

    session = test_db()
    assert session.query(UserGoal).one().target_date is None
    session.close()


def test_countdown_is_shown(logged_client):
    from datetime import timedelta

    future = (date.today() + timedelta(days=20)).isoformat()
    logged_client.post("/goals", data={"goal_type": "race_half", "target_date": future})
    page = logged_client.get("/goals").text
    assert "giorni al traguardo" in page
    assert ">20<" in page.replace(" ", "").replace("\n", "")
