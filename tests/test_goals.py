"""Obiettivi: definizione dei tipi, persistenza, sintesi per i prompt."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app import goals
from app.db.models import User, UserGoal


@pytest.fixture()
def user(db) -> User:
    u = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add(u)
    db.commit()
    return u


# --------------------------- definizione dei tipi ---------------------------

def test_goal_types_have_unique_keys():
    keys = [g.key for g in goals.GOAL_TYPES]
    assert len(keys) == len(set(keys))


def test_goal_params_have_unique_keys_within_type():
    for goal_type in goals.GOAL_TYPES:
        keys = [p.key for p in goal_type.params]
        assert len(keys) == len(set(keys)), goal_type.key


def test_select_params_declare_options():
    for goal_type in goals.GOAL_TYPES:
        for param in goal_type.params:
            if param.kind == "select":
                assert param.options, f"{goal_type.key}.{param.key} senza opzioni"


def test_get_goal_type_unknown_returns_none():
    assert goals.get_goal_type("non-esiste") is None


# --------------------------- parametri ---------------------------

def test_clean_params_keeps_only_declared_keys(db):
    goal_type = goals.get_goal_type("race_10k")
    cleaned = goals.clean_params(goal_type, {"target_time": "50:00", "intruso": "x"})
    assert cleaned == {"target_time": "50:00"}


def test_clean_params_converts_numbers_and_commas(db):
    goal_type = goals.get_goal_type("weight")
    cleaned = goals.clean_params(goal_type, {"target_weight": "72", "weekly_rate": "0,4"})
    assert cleaned == {"target_weight": 72.0, "weekly_rate": 0.4}


def test_clean_params_drops_empty_and_invalid(db):
    goal_type = goals.get_goal_type("weight")
    cleaned = goals.clean_params(goal_type, {"target_weight": "", "weekly_rate": "molto"})
    assert cleaned == {}


# --------------------------- persistenza ---------------------------

def test_set_active_goal_creates_it(db, user):
    goal = goals.set_active_goal(
        db, user, "race_10k",
        target_date=date(2026, 10, 4),
        params={"target_time": "50:00", "weekly_km": "35"},
        priority_notes="Ginocchio destro delicato",
    )
    assert goal.active is True
    assert goal.title == "Gara 10K"
    assert goal.params_json == {"target_time": "50:00", "weekly_km": 35.0}
    assert goal.priority_notes == "Ginocchio destro delicato"
    assert goals.active_goal(db, user.id).id == goal.id


def test_setting_a_new_goal_archives_the_previous(db, user):
    first = goals.set_active_goal(db, user, "race_5k")
    second = goals.set_active_goal(db, user, "race_marathon")

    db.refresh(first)
    assert first.active is False and first.closed_at is not None
    assert second.active is True
    assert goals.active_goal(db, user.id).id == second.id
    assert [g.id for g in goals.past_goals(db, user.id)] == [first.id]


def test_custom_title_wins_over_default(db, user):
    goal = goals.set_active_goal(db, user, "custom", title="100 km in un mese")
    assert goal.title == "100 km in un mese"


def test_unknown_goal_type_is_rejected(db, user):
    with pytest.raises(ValueError):
        goals.set_active_goal(db, user, "inventato")


def test_close_goal(db, user):
    goals.set_active_goal(db, user, "race_5k")
    closed = goals.close_goal(db, user.id, outcome="Fatta in 24:31!")
    assert closed.active is False and closed.outcome == "Fatta in 24:31!"
    assert goals.active_goal(db, user.id) is None


def test_close_goal_when_none_active(db, user):
    assert goals.close_goal(db, user.id) is None


def test_goals_are_isolated_between_users(db, user):
    other = User(garmin_email="b@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add(other)
    db.commit()

    goals.set_active_goal(db, user, "race_5k")
    goals.set_active_goal(db, other, "race_marathon")

    assert goals.active_goal(db, user.id).goal_type == "race_5k"
    assert goals.active_goal(db, other.id).goal_type == "race_marathon"


def test_goal_without_target_date_ignores_it(db, user):
    goal = goals.set_active_goal(db, user, "sleep", target_date=None)
    assert goal.target_date is None


# --------------------------- sintesi per i prompt ---------------------------

def test_describe_no_goal():
    assert goals.describe_goal(None) == "Nessun obiettivo impostato."


def test_describe_includes_params_units_and_countdown(db, user):
    today = date(2026, 8, 14)
    goal = goals.set_active_goal(
        db, user, "race_10k",
        target_date=today + timedelta(days=30),
        params={"target_time": "50:00", "weekly_km": "35"},
    )
    text = goals.describe_goal(goal, today=today)
    assert "Gara 10K" in text
    assert "50:00" in text
    assert "35 km" in text
    assert "mancano 30 giorni" in text


def test_describe_handles_today_and_past_dates(db, user):
    today = date(2026, 8, 14)
    goal = goals.set_active_goal(db, user, "race_5k", target_date=today)
    assert "è oggi" in goals.describe_goal(goal, today=today)

    goal.target_date = today - timedelta(days=5)
    assert "superata di 5 giorni" in goals.describe_goal(goal, today=today)


def test_describe_includes_notes(db, user):
    goal = goals.set_active_goal(db, user, "fitness", priority_notes="Poco tempo il lunedì")
    assert "Poco tempo il lunedì" in goals.describe_goal(goal)


def test_days_to_target_none_without_date(db, user):
    goal = goals.set_active_goal(db, user, "fitness")
    assert goals.days_to_target(goal) is None
