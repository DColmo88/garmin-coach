"""TRIMP, curve fitness/fatica/forma, rapporto acuto/cronico."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.analysis.load import (
    ACWR_DANGER,
    FROM_DURATION,
    FROM_HR,
    FROM_POWER,
    acwr,
    acwr_label,
    activity_load,
    daily_loads,
    form_label,
    MONOTONY_HIGH,
    MONOTONY_MAX,
    monotony,
    pmc_series,
    strain,
    trimp,
)
from app.analysis.profile import AthleteProfile


@pytest.fixture()
def profile() -> AthleteProfile:
    return AthleteProfile(hr_max=190, hr_rest=50, lthr=167, ftp=250, sex="m")


def _act(**kw):
    return SimpleNamespace(
        duration_sec=kw.pop("duration_sec", 3600),
        avg_hr=kw.pop("avg_hr", None),
        avg_power=kw.pop("avg_power", None),
        start_time=kw.pop("start_time", None),
        **kw,
    )


# --------------------------- TRIMP ---------------------------

def test_trimp_grows_faster_than_intensity(profile):
    """L'esponenziale è il punto: raddoppiare lo sforzo costa più del doppio."""
    easy = trimp(3600, 106, profile)    # 40% della riserva
    hard = trimp(3600, 162, profile)    # 80% della riserva

    assert easy is not None and hard is not None
    assert hard > 2 * easy


def test_trimp_scales_linearly_with_time(profile):
    one_hour = trimp(3600, 150, profile)
    two_hours = trimp(7200, 150, profile)
    assert two_hours == pytest.approx(2 * one_hour)


def test_trimp_needs_both_duration_and_heart_rate(profile):
    assert trimp(None, 150, profile) is None
    assert trimp(3600, None, profile) is None


def test_heart_rate_below_rest_does_not_go_negative(profile):
    """Un errore di misura non deve produrre un carico negativo."""
    assert trimp(3600, 30, profile) == 0.0


def test_heart_rate_above_max_is_capped(profile):
    """Sopra la massima dichiarata il modello si ferma alla massima."""
    at_max = trimp(3600, 190, profile)
    absurd = trimp(3600, 240, profile)
    assert at_max == pytest.approx(absurd)


# --------------------------- scelta della fonte ---------------------------

def test_power_wins_over_heart_rate(profile):
    load = activity_load(_act(duration_sec=3600, avg_hr=150, avg_power=250), profile)
    assert load.source == FROM_POWER
    assert load.value == pytest.approx(100)  # un'ora a FTP = 100 per definizione


def test_heart_rate_used_when_there_is_no_power_meter(profile):
    load = activity_load(_act(duration_sec=3600, avg_hr=150), profile)
    assert load.source == FROM_HR
    assert load.is_measured


def test_power_ignored_without_a_declared_ftp():
    """Senza FTP la potenza non è confrontabile con niente: si passa alla FC."""
    no_ftp = AthleteProfile(hr_max=190, hr_rest=50, lthr=167, ftp=None)
    load = activity_load(_act(duration_sec=3600, avg_hr=150, avg_power=250), no_ftp)
    assert load.source == FROM_HR


def test_duration_only_is_marked_as_not_measured(profile):
    load = activity_load(_act(duration_sec=3600), profile)
    assert load.source == FROM_DURATION
    assert not load.is_measured


def test_an_activity_without_duration_has_no_load(profile):
    assert activity_load(_act(duration_sec=None), profile) is None


# --------------------------- aggregazione giornaliera ---------------------------

def test_two_sessions_in_one_day_are_summed(profile):
    day = datetime(2026, 5, 4, 7, 0)
    loads = daily_loads([
        _act(duration_sec=3600, avg_hr=150, start_time=day),
        _act(duration_sec=1800, avg_hr=150, start_time=day.replace(hour=18)),
    ], profile)

    assert list(loads) == [date(2026, 5, 4)]
    single = activity_load(_act(duration_sec=3600, avg_hr=150), profile).value
    assert loads[date(2026, 5, 4)] == pytest.approx(single * 1.5)


def test_activities_without_a_date_are_skipped(profile):
    assert daily_loads([_act(duration_sec=3600, avg_hr=150)], profile) == {}


# --------------------------- Performance Management Chart ---------------------------

def test_fatigue_reacts_faster_than_fitness():
    """Dopo due settimane facili, una dura alza la fatica molto più della fitness."""
    start = date(2026, 1, 1)
    loads = {start + timedelta(days=i): 20.0 for i in range(14)}
    loads.update({start + timedelta(days=i): 150.0 for i in range(14, 21)})

    points = pmc_series(loads, start, start + timedelta(days=20))

    last = points[-1]
    assert last.atl > last.ctl
    assert last.tsb < 0  # il saldo è in rosso: si è speso più di quanto si è costruito


def test_rest_makes_form_rise():
    """È nei giorni vuoti che la forma torna: la fatica scende più della fitness."""
    start = date(2026, 1, 1)
    loads = {start + timedelta(days=i): 120.0 for i in range(14)}
    points = pmc_series(loads, start, start + timedelta(days=27))  # due settimane di stop

    hard_day = points[13]
    rested_day = points[-1]
    assert rested_day.tsb > hard_day.tsb


def test_series_covers_every_day_including_the_empty_ones():
    start, end = date(2026, 3, 1), date(2026, 3, 10)
    points = pmc_series({date(2026, 3, 5): 200.0}, start, end)

    assert [p.day for p in points] == [start + timedelta(days=i) for i in range(10)]
    assert points[0].load == 0.0
    assert points[4].load == 200.0


def test_the_curves_do_not_start_from_zero():
    """Partire da zero disegnerebbe una crescita che è solo l'inizio della finestra."""
    start = date(2026, 1, 1)
    loads = {start + timedelta(days=i): 80.0 for i in range(30)}
    points = pmc_series(loads, start, start + timedelta(days=29))

    assert points[0].ctl > 0
    # con carico costante la fitness resta piatta invece di impennarsi
    assert points[-1].ctl == pytest.approx(points[0].ctl, rel=0.05)


def test_empty_input_gives_an_empty_series():
    assert pmc_series({}, date(2026, 1, 5), date(2026, 1, 1)) == []


# --------------------------- ACWR ---------------------------

def test_acwr_is_one_when_the_load_is_steady():
    end = date(2026, 6, 30)
    loads = {end - timedelta(days=i): 50.0 for i in range(28)}
    assert acwr(loads, end) == pytest.approx(1.0)


def test_a_sudden_spike_pushes_acwr_into_the_danger_zone():
    end = date(2026, 6, 30)
    loads = {end - timedelta(days=i): 20.0 for i in range(7, 28)}
    loads.update({end - timedelta(days=i): 120.0 for i in range(7)})

    ratio = acwr(loads, end)
    assert ratio is not None and ratio >= ACWR_DANGER
    assert acwr_label(ratio)[1] == "bad"


def test_acwr_is_none_without_history():
    """Senza uno storico non esiste un «troppo in fretta» rispetto a cui misurarsi."""
    assert acwr({}, date(2026, 6, 30)) is None


def test_a_sporadic_athlete_is_not_accused_of_overtraining():
    """Il caso che si è visto sui dati veri: una sola uscita in tre settimane.

    Il rapporto verrebbe 3, non perché ci sia un rischio ma perché il
    denominatore è quasi zero. Meglio non dire niente.
    """
    end = date(2026, 6, 30)
    loads = {end: 44.0, end - timedelta(days=17): 22.0, end - timedelta(days=26): 15.0}

    assert acwr(loads, end) is None


def test_eight_training_days_are_enough_to_start_measuring():
    end = date(2026, 6, 30)
    loads = {end - timedelta(days=i * 3): 50.0 for i in range(9)}  # ~2 a settimana

    assert acwr(loads, end) is not None


# --------------------------- monotonia ---------------------------

def test_a_week_all_the_same_is_more_monotonous_than_a_varied_one():
    end = date(2026, 6, 30)
    flat = {end - timedelta(days=i): load
            for i, load in enumerate([62, 58, 60, 61, 59, 60, 60])}
    varied = {end - timedelta(days=i): load
              for i, load in enumerate([200, 0, 60, 0, 140, 20, 0])}

    assert monotony(flat, end) > monotony(varied, end)


def test_seven_identical_days_are_the_worst_case_not_the_unknown_case():
    """Variabilità zero è monotonia massima, non monotonia incalcolabile.

    Prima tornava `None`: la settimana più monotona che esista era l'unica su
    cui l'app non diceva niente, cioè proprio quella che la metrica serve a
    segnalare.
    """
    end = date(2026, 6, 30)
    flat = {end - timedelta(days=i): 60.0 for i in range(7)}
    assert monotony(flat, end) == MONOTONY_MAX
    assert monotony(flat, end) > MONOTONY_HIGH


def test_strain_punishes_the_same_volume_spread_evenly():
    """Stesso carico settimanale: distribuito male, logora di più."""
    end = date(2026, 6, 30)
    flat = {end - timedelta(days=i): load
            for i, load in enumerate([62, 58, 60, 61, 59, 60, 60])}
    varied = {end - timedelta(days=i): load
              for i, load in enumerate([200, 0, 60, 0, 140, 20, 0])}

    assert sum(flat.values()) == sum(varied.values())
    assert strain(flat, end) > strain(varied, end)


def test_a_week_without_training_has_no_monotony():
    assert monotony({}, date(2026, 6, 30)) is None
    assert strain({}, date(2026, 6, 30)) is None


# --------------------------- letture ---------------------------

def test_form_reads_both_extremes_as_problems():
    assert form_label(60)[1] == "warn"    # troppo fresco: si perde il lavoro fatto
    assert form_label(-50)[1] == "bad"    # in buca
    assert form_label(0)[1] == "good"


def test_labels_survive_missing_numbers():
    assert acwr_label(None)[1] == "neutral"
    assert form_label(None)[1] == "neutral"


def test_monotony_needs_more_than_a_couple_of_sessions():
    """Con una sola uscita in sette giorni il rapporto esce, ma non descrive niente."""
    end = date(2026, 6, 30)
    assert monotony({end: 44.0}, end) is None
    assert monotony({end: 44.0, end - timedelta(days=3): 30.0}, end) is None
    assert monotony(
        {end: 44.0, end - timedelta(days=3): 30.0, end - timedelta(days=5): 60.0}, end
    ) is not None
