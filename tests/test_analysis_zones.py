"""Distribuzione delle intensità a partire dal tempo per zona."""
from __future__ import annotations

from types import SimpleNamespace

from app.analysis.zones import (
    BOUNDS_FROM_DEVICE,
    BOUNDS_FROM_HR_MAX,
    observed_bounds,
    ZONES,
    read_distribution,
    zone_boundaries,
    zone_distribution,
)


def _act(zones=None):
    return SimpleNamespace(hr_zones_json=zones)


def _minutes(*mins):
    return [m * 60 for m in mins]


def test_seconds_are_summed_zone_by_zone():
    dist = zone_distribution([
        _act(_minutes(10, 20, 5, 5, 0)),
        _act(_minutes(5, 30, 10, 0, 5)),
    ])

    assert dist.seconds == _minutes(15, 50, 15, 5, 5)
    assert dist.activities_counted == 2


def test_activities_without_the_datum_lower_the_coverage_not_the_percentages():
    """Un'attività senza zone non deve entrare come una fatta tutta in zona 1."""
    with_data = _act(_minutes(0, 60, 0, 0, 0))
    dist = zone_distribution([with_data, _act(None), _act([])])

    assert dist.easy_pct == 100
    assert dist.activities_counted == 1
    assert dist.coverage < 0.4


def test_an_empty_history_says_so_instead_of_dividing_by_zero():
    dist = zone_distribution([])
    assert not dist.has_data
    assert dist.percentages == [0.0] * 5
    assert dist.easy_pct == 0


def test_the_three_bands_add_up_to_the_whole():
    dist = zone_distribution([_act(_minutes(30, 60, 20, 15, 5))])
    assert dist.easy_pct + dist.grey_pct + dist.hard_pct == 100


def test_boundaries_follow_the_declared_maximum():
    bounds, source = zone_boundaries(200)

    assert bounds[0] == (100, 120)
    assert len(bounds) == len(ZONES)
    assert source == BOUNDS_FROM_HR_MAX


def test_the_watch_boundaries_win_over_the_derived_ones():
    """I secondi li conta l'orologio con le zone che ha impostate.

    Se quelle zone sono tarate sulla soglia o sulla riserva cardiaca, i confini
    ricavati dalla FC massima non sono quelli con cui i minuti del grafico sono
    stati sommati: la legenda diceva una cosa e le barre un'altra.
    """
    dal_dispositivo = [95, 121, 140, 158, 172]

    bounds, source = zone_boundaries(200, dal_dispositivo)

    assert source == BOUNDS_FROM_DEVICE
    assert bounds[0] == (95, 121)
    assert bounds[4] == (172, 200)


def test_incomplete_watch_boundaries_fall_back_and_say_so():
    bounds, source = zone_boundaries(200, [95, None, None, None, None])

    assert source == BOUNDS_FROM_HR_MAX
    assert bounds[0] == (100, 120)


def test_the_most_recent_activity_wins():
    """Se l'atleta ha cambiato le zone, quelle giuste sono le ultime."""
    from types import SimpleNamespace

    recente = SimpleNamespace(hr_zone_bounds_json=[100, 125, 145, 165, 180])
    vecchia = SimpleNamespace(hr_zone_bounds_json=[90, 115, 135, 155, 170])
    senza = SimpleNamespace(hr_zone_bounds_json=None)

    assert observed_bounds([senza, recente, vecchia]) == recente.hr_zone_bounds_json
    assert observed_bounds([senza]) is None
    assert observed_bounds([]) is None


# --------------------------- lettura ---------------------------

def test_too_little_data_is_not_commented():
    dist = zone_distribution([_act(_minutes(10, 20, 0, 0, 0))])
    text, tone = read_distribution(dist)
    assert tone == "neutral"
    assert "più allenamenti" in text


def test_the_grey_zone_is_called_out():
    """Il caso che l'app deve saper vedere: tutto a intensità media."""
    dist = zone_distribution([_act(_minutes(20, 60, 120, 10, 0))])
    text, tone = read_distribution(dist)
    assert tone == "warn"
    assert "zona 3" in text


def test_a_polarised_distribution_is_recognised():
    dist = zone_distribution([_act(_minutes(120, 300, 20, 40, 20))])
    text, tone = read_distribution(dist)
    assert tone == "good"


def test_all_easy_and_no_quality_is_flagged():
    dist = zone_distribution([_act(_minutes(200, 300, 10, 2, 0))])
    text, tone = read_distribution(dist)
    assert tone == "warn"
    assert "stimolo" in text
