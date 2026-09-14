"""Le letture per pagina: verdetti corretti, soglie, tolleranza ai buchi."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.insights import domains, stats as st


# --------------------------- costruttori di dati ---------------------------

def sleep_rows(nights: int = 14, hours: float = 7.5, score: float = 80,
               deep_frac: float = 0.20, rem_frac: float = 0.22,
               jitter_min: float = 0):
    rows = []
    for i in range(nights):
        seconds = hours * 3600 + (jitter_min * 60 * (1 if i % 2 else -1))
        rows.append(SimpleNamespace(
            day=date(2026, 8, 1) + timedelta(days=i),
            total_sleep_sec=seconds,
            deep_sleep_sec=seconds * deep_frac,
            rem_sleep_sec=seconds * rem_frac,
            sleep_score=score,
        ))
    return rows


def wellness_rows(days: int = 28, rhr: float = 52, battery: float = 90,
                  stress: float = 30, steps: int = 9000, rhr_recent: float | None = None):
    rows = []
    for i in range(days):
        is_recent = i >= days - 7
        rows.append(SimpleNamespace(
            day=date(2026, 8, 1) + timedelta(days=i),
            resting_hr=(rhr_recent if is_recent and rhr_recent is not None else rhr),
            body_battery_high=battery,
            avg_stress=stress,
            total_steps=steps,
        ))
    return rows


def training_rows(days: int = 28, vo2: float = 50, load: float = 250,
                  vo2_recent: float | None = None, load_recent: float | None = None,
                  status: str = "productive", hrv_status: str = "balanced"):
    rows = []
    for i in range(days):
        is_recent = i >= days - 7
        rows.append(SimpleNamespace(
            day=date(2026, 8, 1) + timedelta(days=i),
            vo2max=(vo2_recent if is_recent and vo2_recent is not None else vo2),
            training_load=(load_recent if is_recent and load_recent is not None else load),
            hrv_weekly_avg=65,
            hrv_status=hrv_status,
            training_status=status,
        ))
    return rows


def body_rows(days: int = 28, start_kg: float = 75, per_day_kg: float = 0,
              fat: float = 18, muscle_kg: float = 35, muscle_recent_kg: float | None = None):
    rows = []
    for i in range(days):
        is_recent = i >= days - 7
        rows.append(SimpleNamespace(
            day=date(2026, 8, 1) + timedelta(days=i),
            weight_g=(start_kg + per_day_kg * i) * 1000,
            bmi=22.0,
            body_fat_pct=fat,
            muscle_mass_g=((muscle_recent_kg if is_recent and muscle_recent_kg is not None
                            else muscle_kg) * 1000),
        ))
    return rows


def activities(count: int = 12, days_span: int = 28, km: float = 8,
               avg_hr: float | None = 140, max_hr: float | None = 180,
               aerobic_te: float | None = None):
    rows = []
    for i in range(count):
        rows.append(SimpleNamespace(
            external_id=i,
            activity_type="running",
            start_time=datetime(2026, 8, 1) + timedelta(days=i * days_span // max(count, 1)),
            distance_m=km * 1000,
            duration_sec=km * 360,
            avg_hr=avg_hr,
            max_hr=max_hr,
            aerobic_te=aerobic_te,
        ))
    return rows


# ============================================================================
# Statistica di base
# ============================================================================

def test_stats_ignore_missing_values():
    assert st.mean([10, None, 20]) == 15
    assert st.mean([None, None]) is None
    assert st.coverage([1, None, None, 4]) == 0.5


def test_slope_needs_enough_points():
    assert st.slope([1, 2, 3]) is None  # sotto la soglia
    assert st.slope([1, 2, 3, 4, 5]) == pytest.approx(1.0)
    assert st.slope([5, 4, 3, 2, 1]) == pytest.approx(-1.0)


def test_slope_of_flat_series_is_zero():
    assert st.slope([7, 7, 7, 7, 7, 7]) == pytest.approx(0.0)


def test_split_separates_recent_from_earlier():
    recent, earlier = st.split(list(range(10)), 3)
    assert recent == [7, 8, 9] and earlier == list(range(7))


def test_fmt_uses_italian_conventions():
    assert st.fmt(1234) == "1.234"
    assert st.fmt(3.75, 1) == "3,8"
    assert st.fmt(None) == "—"
    assert st.fmt_duration(7 * 3600 + 20 * 60) == "7h 20m"


# ============================================================================
# Sonno
# ============================================================================

def test_sleep_without_data():
    reading = domains.read_sleep([])
    assert "abbastanza dati" in reading.verdict
    assert reading.tone == "neutral"


def test_sleep_good_quality_short_duration(db=None):
    reading = domains.read_sleep(sleep_rows(hours=6.0, score=82))
    assert reading.verdict == "Dormi bene, ma poco."
    assert reading.tone == "warn"
    assert "Anticipa" in reading.action


def test_sleep_enough_hours_poor_quality():
    reading = domains.read_sleep(sleep_rows(hours=8.0, score=50))
    assert "non ristora" in reading.verdict
    assert reading.tone == "warn"


def test_sleep_short_and_poor_is_the_worst_case():
    reading = domains.read_sleep(sleep_rows(hours=5.5, score=45))
    assert reading.verdict == "Il sonno è il tuo anello debole."
    assert reading.tone == "bad"


def test_sleep_irregular_is_flagged_even_when_averages_are_fine():
    """La media giusta con orari ballerini non è sonno buono."""
    reading = domains.read_sleep(sleep_rows(hours=7.5, score=82, jitter_min=90))
    assert "irregolari" in reading.verdict
    assert any("oscilla" in n.text for n in reading.notes)


def test_sleep_all_good():
    reading = domains.read_sleep(sleep_rows(hours=7.8, score=85))
    assert reading.verdict == "Il sonno è in ordine."
    assert reading.tone == "good"


def test_sleep_flags_low_deep_phase():
    reading = domains.read_sleep(sleep_rows(deep_frac=0.10))
    deep_notes = [n for n in reading.notes if "profondo" in n.text]
    assert deep_notes and deep_notes[0].tone == "warn"


def test_sleep_flags_low_rem():
    reading = domains.read_sleep(sleep_rows(rem_frac=0.10))
    assert any("REM" in n.text for n in reading.notes)


# ============================================================================
# Salute
# ============================================================================

def test_health_without_data():
    assert "abbastanza dati" in domains.read_health([]).verdict


def test_health_rising_hr_and_poor_recovery_is_the_alarm():
    reading = domains.read_health(wellness_rows(rhr=50, rhr_recent=58, battery=50))
    assert reading.verdict == "Stai accumulando fatica."
    assert reading.tone == "bad"
    assert "due giorni facili" in reading.action


def test_health_falling_hr_is_adaptation():
    reading = domains.read_health(wellness_rows(rhr=56, rhr_recent=50))
    assert "adattando" in reading.verdict
    assert reading.tone == "good"
    assert any("scesa" in n.text for n in reading.notes)


def test_health_poor_recharge_alone():
    reading = domains.read_health(wellness_rows(battery=50))
    assert "non ti ricarichi" in reading.verdict.lower()


def test_health_stable_is_reported_as_such():
    reading = domains.read_health(wellness_rows())
    assert reading.tone == "good"
    assert "stabili" in reading.verdict


def test_health_flags_high_stress_and_low_steps():
    reading = domains.read_health(wellness_rows(stress=60, steps=3000))
    texts = " ".join(n.text for n in reading.notes)
    assert "Stress medio" in texts and "passi al giorno" in texts


# ============================================================================
# Forma e carico
# ============================================================================

def fake_load(acwr=1.0, tsb=0.0, ctl=45.0, monotony=1.2,
              has_data=True, reliable=True):
    """Un `LoadSummary` finto: qui si testa la lettura, non il calcolo.

    Il calcolo ha i suoi test in `test_analysis_load.py`.
    """
    from app.analysis.load import acwr_label, form_label

    return SimpleNamespace(
        has_data=has_data, acwr=acwr, tsb=tsb, ctl=ctl, atl=ctl - tsb,
        monotony=monotony, is_reliable=reliable,
        form_reading=form_label(tsb), acwr_reading=acwr_label(acwr),
    )


def test_fitness_without_data():
    assert "abbastanza dati" in domains.read_fitness([]).verdict


def test_fitness_reads_without_a_load_summary():
    """La pagina deve reggere anche prima che ci siano attività da cui calcolare."""
    reading = domains.read_fitness(training_rows(vo2=50, vo2_recent=51.5))
    assert reading.has_content
    assert reading.verdict == "La fitness sta salendo."


def test_overload_on_top_of_a_hole_is_the_worst_case():
    reading = domains.read_fitness(training_rows(), fake_load(acwr=1.8, tsb=-40))
    assert reading.tone == "bad"
    assert "continuando a caricare" in reading.verdict
    assert "taglia il volume" in reading.action


def test_overload_alone_says_consolidate():
    reading = domains.read_fitness(training_rows(), fake_load(acwr=1.6, tsb=-5))
    assert "più in fretta" in reading.verdict
    assert reading.tone == "warn"


def test_being_buried_without_overload_asks_for_easy_days():
    reading = domains.read_fitness(training_rows(), fake_load(acwr=1.0, tsb=-45))
    assert "fatica ha superato" in reading.verdict
    assert "due giorni facili" in reading.action


def test_peak_form_is_called_an_opportunity():
    reading = domains.read_fitness(training_rows(), fake_load(acwr=0.7, tsb=32))
    assert reading.tone == "good"
    assert "fresco" in reading.verdict
    assert "gara" in reading.action


def test_fitness_improving_with_sustainable_load():
    reading = domains.read_fitness(
        training_rows(vo2=50, vo2_recent=51.5), fake_load()
    )
    assert reading.verdict == "La fitness sta salendo."
    assert reading.tone == "good"


def test_fitness_declining():
    reading = domains.read_fitness(training_rows(vo2=52, vo2_recent=50), fake_load())
    assert "calando" in reading.verdict


def test_fitness_flags_unbalanced_hrv():
    reading = domains.read_fitness(training_rows(hrv_status="unbalanced"), fake_load())
    hrv_notes = [n for n in reading.notes if "HRV" in n.text]
    assert hrv_notes and hrv_notes[0].tone == "warn"


def test_the_load_ratio_becomes_a_note():
    reading = domains.read_fitness(training_rows(), fake_load(acwr=1.6))
    load_notes = [n for n in reading.notes if n.metric == "load"]
    assert load_notes and load_notes[0].tone == "bad"


def test_a_monotonous_week_becomes_a_note():
    reading = domains.read_fitness(training_rows(), fake_load(monotony=2.5))
    assert any("Monotonia" in n.text for n in reading.notes)


def test_estimated_load_is_declared_in_the_notes():
    """Se metà dei carichi è stimata, la pagina lo dice invece di far finta."""
    reading = domains.read_fitness(training_rows(), fake_load(reliable=False))
    assert any("stimato" in n.text for n in reading.notes)


def test_a_load_summary_without_data_is_ignored_not_crashed_on():
    reading = domains.read_fitness(training_rows(), fake_load(has_data=False))
    assert reading.has_content


# ============================================================================
# Corpo
# ============================================================================

def test_body_needs_several_measurements():
    reading = domains.read_body(body_rows(days=2))
    assert "più misurazioni" in reading.verdict


def test_body_losing_weight_and_muscle_is_flagged():
    """Il caso che conta: la bilancia scende ma per il motivo sbagliato."""
    reading = domains.read_body(
        body_rows(per_day_kg=-0.05, muscle_kg=36, muscle_recent_kg=35.4)
    )
    assert reading.verdict == "Stai perdendo peso, ma anche muscolo."
    assert reading.tone == "bad"
    assert "proteine" in reading.action


def test_body_too_fast_loss():
    reading = domains.read_body(body_rows(per_day_kg=-0.15))
    assert "troppo in fretta" in reading.verdict
    assert reading.tone == "warn"


def test_body_healthy_loss():
    reading = domains.read_body(body_rows(per_day_kg=-0.05))
    assert reading.tone == "good"
    assert "sostenibile" in reading.verdict


def test_body_gaining_weight():
    reading = domains.read_body(body_rows(per_day_kg=0.05))
    assert "salendo" in reading.verdict


def test_body_stable():
    reading = domains.read_body(body_rows(per_day_kg=0))
    assert reading.verdict == "Peso stabile."


# ============================================================================
# Attività — distribuzione delle intensità
# ============================================================================

def test_activities_need_a_few_sessions():
    assert "abbastanza dati" in domains.read_activities([]).verdict
    assert "abbastanza dati" in domains.read_activities(activities(count=2)).verdict


def test_activities_flags_the_grey_zone_trap():
    """Tutte le uscite a intensità media: l'errore più comune degli amatori."""
    # 140/180 = 78% → fascia media
    reading = domains.read_activities(activities(count=12, avg_hr=140, max_hr=180))
    assert "stessa intensità media" in reading.verdict
    assert reading.tone == "warn"
    assert "parlare" in reading.action


def test_activities_recognises_good_polarization():
    easy = activities(count=9, avg_hr=120, max_hr=180)      # 67% → facile
    hard = activities(count=3, avg_hr=160, max_hr=180)      # 89% → duro
    reading = domains.read_activities(easy + hard)
    assert reading.tone == "good"
    assert "distribuzione delle intensità" in reading.verdict


def test_activities_low_volume():
    reading = domains.read_activities(activities(count=4, days_span=28, avg_hr=120))
    assert "volume è basso" in reading.verdict


def test_activities_use_training_effect_when_hr_is_missing():
    """Senza FC si ripiega sul training effect di Garmin."""
    reading = domains.read_activities(
        activities(count=10, avg_hr=None, max_hr=None, aerobic_te=1.5)
    )
    assert any("facil" in n.text for n in reading.notes)


def test_activities_without_any_intensity_signal_still_reads():
    reading = domains.read_activities(
        activities(count=10, avg_hr=None, max_hr=None, aerobic_te=None)
    )
    assert reading.has_content
    assert "regolarità" in reading.verdict


def test_activities_report_volume_and_longest():
    reading = domains.read_activities(activities(count=12, km=10))
    texts = " ".join(n.text for n in reading.notes)
    assert "allenamenti a settimana" in texts
    assert "Uscita più lunga" in texts


# ============================================================================
# Robustezza generale
# ============================================================================

@pytest.mark.parametrize("reader,rows", [
    (domains.read_sleep, sleep_rows()),
    (domains.read_health, wellness_rows()),
    (domains.read_fitness, training_rows()),
    (domains.read_body, body_rows()),
])
def test_every_reading_is_complete(reader, rows):
    """Ogni lettura ha sempre verdetto, evidenza e azione."""
    reading = reader(rows)
    assert reading.verdict and reading.evidence and reading.action
    assert reading.tone in {"good", "warn", "bad", "neutral"}


@pytest.mark.parametrize("reader,rows", [
    (domains.read_sleep, sleep_rows()),
    (domains.read_health, wellness_rows()),
    (domains.read_fitness, training_rows()),
    (domains.read_body, body_rows()),
])
def test_readings_survive_holes_in_the_data(reader, rows):
    """I dati Garmin hanno buchi: nessuna lettura deve esplodere."""
    for i, row in enumerate(rows):
        if i % 3 == 0:
            for attribute in vars(row):
                if attribute != "day":
                    setattr(row, attribute, None)
    assert reader(rows).has_content


def test_note_tones_are_valid():
    for reader, rows in (
        (domains.read_sleep, sleep_rows(deep_frac=0.10)),
        (domains.read_health, wellness_rows(rhr=50, rhr_recent=58, battery=50)),
        (domains.read_fitness, training_rows()),
        (domains.read_body, body_rows(per_day_kg=-0.15)),
        (domains.read_activities, activities()),
    ):
        for note in reader(rows).notes:
            assert note.tone in {"good", "warn", "bad", "neutral"}
            assert note.metric and note.text


# ============================================================================
# Testi: grammatica e sigle
# ============================================================================

def test_no_data_messages_are_grammatical():
    for reader, empty in (
        (domains.read_sleep, []),
        (domains.read_health, []),
        (domains.read_fitness, []),
        (domains.read_activities, []),
    ):
        verdict = reader(empty).verdict
        assert "dati sul performance" not in verdict
        assert "dati sul volume" not in verdict
        # deve leggersi come una frase italiana corretta
        assert verdict.startswith("Non ho ancora abbastanza dati ")
        assert verdict.endswith(".")


def test_acronyms_keep_their_capitalisation():
    """`.capitalize()` avrebbe trasformato «FC» in «Fc» e «VO₂max» in «Vo₂max»."""
    health = domains.read_health(wellness_rows())
    assert "FC a riposo" in health.evidence and "Fc a riposo" not in health.evidence

    fitness = domains.read_fitness(training_rows())
    assert "VO₂max" in fitness.evidence and "Vo₂max" not in fitness.evidence


def test_low_volume_wins_over_intensity_distribution():
    """Con una sessione a settimana non ha senso parlare di polarizzazione."""
    reading = domains.read_activities(
        activities(count=4, days_span=28, avg_hr=140, max_hr=180)  # zona grigia
    )
    assert "volume è basso" in reading.verdict


def test_intensity_distribution_matters_once_volume_is_there():
    reading = domains.read_activities(
        activities(count=12, days_span=28, avg_hr=140, max_hr=180)
    )
    assert "stessa intensità media" in reading.verdict


# ============================================================================
# Costanti di Garmin tradotte
# ============================================================================

@pytest.mark.parametrize("raw,expected", [
    ("UNPRODUCTIVE_1", "allenamento improduttivo"),
    ("PRODUCTIVE_2", "allenamento produttivo"),
    ("MAINTAINING", "stai mantenendo"),
    ("peaking", "sei al picco"),
])
def test_training_status_becomes_italian(raw, expected):
    assert domains.training_status_label(raw) == expected


def test_an_unknown_status_is_at_least_readable():
    """Garmin ne inventa di nuovi: meglio ripulirlo che mostrarlo com'è."""
    assert domains.training_status_label("SOMETHING_NEW_3") == "something new"


def test_a_missing_status_stays_missing():
    assert domains.training_status_label(None) is None
    assert domains.training_status_label("") is None


def test_the_raw_constant_never_reaches_the_prose():
    reading = domains.read_fitness(training_rows(status="UNPRODUCTIVE_1"))
    assert "UNPRODUCTIVE_1" not in reading.evidence
    assert "improduttivo" in reading.evidence


def test_hrv_status_becomes_italian():
    assert domains.hrv_status_label("BALANCED") == "in equilibrio"
    assert domains.hrv_status_label("UNBALANCED") == "sbilanciato"
    assert domains.hrv_status_label(None) is None


def test_the_raw_hrv_constant_never_reaches_the_notes():
    reading = domains.read_fitness(training_rows(hrv_status="BALANCED"))
    hrv_notes = [n for n in reading.notes if n.metric == "hrv"]
    assert hrv_notes
    assert "BALANCED" not in hrv_notes[0].text
    assert "in equilibrio" in hrv_notes[0].text
