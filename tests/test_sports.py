"""Gli sport: nome, icona, unità di misura, sport principale.

Il difetto da cui nasce questo modulo: in tabella si leggeva `indoor_cycling`,
e un giro in bici a 23 km/h veniva mostrato come «2:35/km».
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app import sports


def _act(activity_type=None, km=None, minutes=None):
    return SimpleNamespace(
        activity_type=activity_type,
        distance_m=km * 1000 if km else None,
        duration_sec=minutes * 60 if minutes else None,
    )


# --------------------------- riconoscimento ---------------------------

@pytest.mark.parametrize("raw,family,label", [
    ("running", sports.RUNNING, "Corsa"),
    ("trail_running", sports.RUNNING, "Trail"),
    ("treadmill_running", sports.RUNNING, "Tapis roulant"),
    ("cycling", sports.CYCLING, "Bici"),
    ("indoor_cycling", sports.CYCLING, "Bici indoor"),
    ("mountain_biking", sports.CYCLING, "MTB"),
    ("gravel_cycling", sports.CYCLING, "Gravel"),
    ("lap_swimming", sports.SWIMMING, "Nuoto"),
    ("open_water_swimming", sports.SWIMMING, "Nuoto in acque libere"),
    ("hiking", sports.WALKING, "Escursione"),
    ("strength_training", sports.STRENGTH, "Forza"),
    ("resort_skiing", sports.OTHER, "Sci"),
])
def test_garmin_codes_become_italian(raw, family, label):
    sport, name = sports.classify(raw)
    assert sport.family == family
    assert name == label


def test_mountain_biking_is_not_mistaken_for_a_walk():
    """Contiene «mountain», ma è una bici. L'ordine dei controlli conta."""
    assert sports.classify("mountain_biking")[0].family == sports.CYCLING


def test_an_unknown_sport_stays_readable():
    sport, label = sports.classify("underwater_basket_weaving")
    assert sport.family == sports.OTHER
    assert label == "Underwater basket weaving"


def test_a_missing_type_does_not_crash():
    sport, label = sports.classify(None)
    assert sport.icon and label == "Attività"


def test_every_sport_has_an_icon():
    for sport in sports.SPORTS.values():
        assert sport.icon.startswith("sport-")
        assert sport.label


# --------------------------- ritmo o velocità ---------------------------

def test_running_is_shown_as_pace():
    assert sports.speed_label(_act("running", km=10, minutes=50)) == "5:00/km"


def test_cycling_is_shown_as_speed():
    """Il difetto originale: «100 km a 2:35/km» invece di «23,1 km/h»."""
    assert sports.speed_label(_act("cycling", km=60, minutes=150)) == "24,0 km/h"


def test_swimming_and_skiing_use_speed_too():
    assert "km/h" in sports.speed_label(_act("lap_swimming", km=2, minutes=40))
    assert "km/h" in sports.speed_label(_act("resort_skiing", km=30, minutes=120))


def test_hiking_keeps_the_pace():
    assert "/km" in sports.speed_label(_act("hiking", km=8, minutes=150))


def test_too_short_or_incomplete_gives_nothing():
    """Meglio una cella vuota di un numero che non vuol dire niente."""
    assert sports.speed_label(_act("running", km=0.2, minutes=2)) == ""
    assert sports.speed_label(_act("running", km=10)) == ""
    assert sports.speed_label(_act("cycling")) == ""


def test_the_column_header_follows_the_mix():
    assert sports.speed_header(["running", "trail_running"]) == "Ritmo"
    assert sports.speed_header(["cycling", "indoor_cycling"]) == "Velocità"
    assert sports.speed_header(["running", "cycling"]) == "Ritmo / vel."


# --------------------------- sport principale ---------------------------

def test_the_default_is_running():
    assert sports.primary_sport(SimpleNamespace(primary_sport=None)) == sports.RUNNING
    assert sports.primary_sport(SimpleNamespace()) == sports.RUNNING


def test_an_invented_value_falls_back():
    assert sports.primary_sport(SimpleNamespace(primary_sport="parapendio")) == sports.RUNNING


def test_a_declared_sport_is_kept():
    assert sports.primary_sport(SimpleNamespace(primary_sport="cycling")) == sports.CYCLING


def test_the_suggestion_counts_time_not_outings():
    """Tre rulli da venti minuti non fanno di uno un ciclista."""
    activities = [
        _act("indoor_cycling", km=10, minutes=20),
        _act("indoor_cycling", km=10, minutes=20),
        _act("indoor_cycling", km=10, minutes=20),
        _act("running", km=15, minutes=80),
    ]
    assert sports.infer_primary_sport(activities) == sports.RUNNING


def test_the_suggestion_sees_a_real_cyclist():
    activities = [
        _act("cycling", km=80, minutes=200),
        _act("running", km=5, minutes=28),
    ]
    assert sports.infer_primary_sport(activities) == sports.CYCLING


def test_without_activities_there_is_nothing_to_suggest():
    assert sports.infer_primary_sport([]) is None
    assert sports.infer_primary_sport([_act("hiking", km=5, minutes=90)]) is None
