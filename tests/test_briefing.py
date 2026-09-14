"""Il briefing che riceve l'AI.

Il bug che tutto questo modulo esiste per risolvere: il modello vedeva «sonno
41» e scriveva «riposa», senza sapere che le sei notti prima erano fra 78 e 84.
Il test che conta davvero è `test_one_bad_night_is_marked_as_one_night`.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from app.ai import briefing
from app.db.models import Activity, DailyWellness, SleepRecord, TrainingMetric, User


@pytest.fixture()
def user(db) -> User:
    u = User(email="a@x.it", display_name="Davide",
             password_hash="h")
    db.add(u)
    db.commit()
    return u


def _seed_nights(db, user, scores: list[float | None], batteries: list[float] | None = None):
    """`scores[0]` è oggi, poi si va indietro."""
    today = date.today()
    for i, score in enumerate(scores):
        db.add(SleepRecord(user_id=user.id, day=today - timedelta(days=i),
                           sleep_score=score, total_sleep_sec=7 * 3600))
        db.add(DailyWellness(
            user_id=user.id, day=today - timedelta(days=i),
            resting_hr=56,
            body_battery_high=(batteries[i] if batteries else 85),
        ))
    db.commit()


# ============================================================================
# Il confronto col proprio normale
# ============================================================================

def test_one_bad_night_is_marked_as_one_night(db, user):
    """Il test che descrive il bug: una notte storta dopo dodici buone."""
    _seed_nights(db, user, [41] + [82] * 13)

    text = briefing.build(db, user)

    assert "Qualità sonno 41/100" in text
    assert "sotto la norma" in text
    assert "fuori norma da 1 giorno" in text
    assert "fuori norma da 2 giorni" not in text


def test_a_bad_week_is_marked_as_a_week(db, user):
    """Simmetrico: cinque giorni di fila fuori norma sono un'altra cosa."""
    _seed_nights(db, user, [45, 47, 44, 46, 43] + [82] * 15)

    text = briefing.build(db, user)

    assert "fuori norma da 5 giorni" in text


def test_a_value_inside_the_personal_range_is_not_alarming(db, user):
    """Chi dorme sempre 60 non deve leggere «male» ogni giorno."""
    _seed_nights(db, user, [60] * 14)

    text = briefing.build(db, user)

    assert "Qualità sonno 60/100" in text
    assert "nella norma" in text
    assert "fuori norma" not in text


def test_the_extreme_of_the_month_is_called_out(db, user):
    _seed_nights(db, user, [30] + [70, 75, 80, 72, 78, 76, 74, 79, 71, 77])

    text = briefing.build(db, user)

    assert "è il minimo degli ultimi 30 giorni" in text


def test_without_enough_history_no_baseline_is_invented(db, user):
    _seed_nights(db, user, [41, 82, 80])

    text = briefing.build(db, user)

    assert "Qualità sonno 41/100" in text
    assert "storico insufficiente" in text
    assert "tipico" not in text


# ============================================================================
# Le serie
# ============================================================================

def test_the_series_shows_fourteen_days(db, user):
    _seed_nights(db, user, list(range(60, 90)))

    text = briefing.build(db, user)
    line = next(l for l in text.splitlines() if l.startswith("Qualità /100"))

    # quattordici celle, col punto che separa le due settimane
    assert line.count("·") == 1
    numbers = [c for c in line.replace("·", " ").split() if c.isdigit()]
    assert len(numbers) == 14


def test_gaps_in_the_series_are_shown_not_filled(db, user):
    """Una notte non registrata non è una notte a zero."""
    _seed_nights(db, user, [80, None, 78] + [82] * 11)

    text = briefing.build(db, user)

    assert "—" in text


# ============================================================================
# Gli allenamenti, che prima non c'erano affatto
# ============================================================================

def _add_run(db, user, days_ago: int, km: float, minutes: int, **kw):
    db.add(Activity(
        user_id=user.id, external_id=7000 + days_ago,
        activity_type=kw.pop("activity_type", "running"),
        start_time=datetime.combine(date.today() - timedelta(days=days_ago),
                                    datetime.min.time()),
        distance_m=km * 1000, duration_sec=minutes * 60,
        avg_hr=kw.pop("avg_hr", 150), max_hr=kw.pop("max_hr", 175),
        **kw,
    ))


def test_the_last_activities_are_always_in_the_briefing(db, user):
    """Prima il modello doveva chiederle con un tool, e spesso non lo faceva."""
    _add_run(db, user, 1, 10.0, 52)
    _add_run(db, user, 4, 6.0, 33)
    db.commit()

    text = briefing.build(db, user)

    assert "ULTIMI 2 ALLENAMENTI" in text
    assert "10,0 km" in text


def test_running_gets_a_pace_and_cycling_a_speed(db, user):
    """«100 km a 2:35/km» è giusto e illeggibile: in bici si ragiona in km/h."""
    _add_run(db, user, 1, 10.0, 50)
    _add_run(db, user, 2, 60.0, 150, activity_type="cycling")
    db.commit()

    text = briefing.build(db, user)

    assert "5:00/km" in text
    assert "24,0 km/h" in text
    assert "2:30/km" not in text


def test_time_in_zones_travels_with_the_activity(db, user):
    _add_run(db, user, 1, 10.0, 50, hr_zones_json=[0, 1800, 900, 300, 0])
    db.commit()

    text = briefing.build(db, user)

    assert "Z2 30'" in text and "Z3 15'" in text
    assert "Z1" not in text.split("ULTIMI")[1], "le zone vuote non vanno stampate"


def test_no_activities_is_said_explicitly(db, user):
    _seed_nights(db, user, [80] * 10)

    text = briefing.build(db, user)

    assert "Nessuno registrato" in text


# ============================================================================
# Robustezza e costo
# ============================================================================

def test_an_empty_user_produces_a_briefing_anyway(db, user):
    text = briefing.build(db, user)

    assert "PROFILO" in text
    assert text.strip()


def test_the_briefing_stays_within_its_budget(db, user):
    """Si paga a ogni messaggio: deve restare intorno ai 600 token."""
    _seed_nights(db, user, [75 + (i % 12) for i in range(30)])
    for i in range(1, 9):
        _add_run(db, user, i * 2, 8.0 + i, 45 + i * 3,
                 hr_zones_json=[300, 1200, 900, 600, 120])
    db.add(TrainingMetric(user_id=user.id, day=date.today(), vo2max=52.4,
                          hrv_status="balanced"))
    db.commit()

    text = briefing.build(db, user)
    tokens = len(text) / 3.6

    assert tokens < 900, f"briefing troppo lungo: ~{tokens:.0f} token"
    assert "OGGI CONTRO IL TUO NORMALE" in text
    assert "ANDAMENTO 14 GIORNI" in text
    assert "ULTIMI 8 ALLENAMENTI" in text


# ============================================================================
# Chi non ha il recupero
# ============================================================================

def test_the_prompt_tells_the_model_it_cannot_know_about_recovery():
    """Senza questo blocco il modello tratta l'assenza come un guasto.

    Peggio: la deduce dal carico, e un TSB alto diventa «hai recuperato bene»
    per qualcuno di cui non sappiamo se ha dormito.
    """
    from app.ai.prompts import chat_system_prompt
    from app.ai.readiness import compute_readiness

    readiness = compute_readiness({})
    prompt = chat_system_prompt("Ada", "BRIEFING", readiness, None, "15/08/2026",
                                has_recovery_data=False)

    assert "non misura niente di notte" in prompt
    assert "chiediglielo" in prompt.lower()
    assert "Non dedurre il recupero dal carico" in prompt


def test_a_user_with_recovery_data_does_not_pay_for_that_block():
    from app.ai.prompts import chat_system_prompt
    from app.ai.readiness import compute_readiness

    readiness = compute_readiness({})
    prompt = chat_system_prompt("Ada", "BRIEFING", readiness, None, "15/08/2026",
                                has_recovery_data=True)

    assert "non misura niente di notte" not in prompt


def test_the_hrv_line_never_lands_on_the_wrong_block(db):
    """Senza dati di benessere l'HRV finiva appiccicato in coda al profilo."""
    from datetime import date

    from app.ai import briefing
    from app.db.models import TrainingMetric, User

    user = User(email="strava@x.it", password_hash="h")
    db.add(user)
    db.flush()
    db.add(TrainingMetric(user_id=user.id, day=date.today(), hrv_status="balanced"))
    db.commit()

    text = briefing.build(db, user)

    assert "PROFILO" in text
    profilo = text.split("\n\n")[0]
    assert "HRV" not in profilo


# ============================================================================
# Le unità: il difetto che ha fatto scrivere «41 minuti di sonno»
# ============================================================================
# Il briefing mandava `Sonno 41` — un punteggio su 100 — senza dire la scala.
# Il modello ha concluso che fossero minuti e l'ha scritto a un atleta che
# quella notte aveva dormito cinque ore. Un numero senza unità chiede di
# essere interpretato, e un modello interpreta sempre.

def test_every_measure_carries_its_unit(db, user):
    _seed_nights(db, user, [70] * 14)

    text = briefing.build(db, user)
    block = next(b for b in text.split("\n\n") if b.startswith("OGGI CONTRO"))

    for line in block.splitlines()[1:]:
        if line.startswith("HRV"):
            continue  # è un'etichetta ("in equilibrio"), non una misura
        assert any(u in line for u in ("/100", " h", " bpm")), f"riga senza unità: {line}"


def test_the_score_can_no_longer_be_read_as_minutes(db, user):
    _seed_nights(db, user, [41] + [82] * 13)

    text = briefing.build(db, user)

    assert "Qualità sonno 41/100" in text
    # «Sonno 41» da solo non compare più da nessuna parte.
    assert "Sonno 41" not in text


def test_the_hours_actually_slept_are_sent(db, user):
    """Il punteggio dice *com'è andata*, non *quanto*.

    Senza le ore, alla domanda «ho dormito poco?» il modello non ha una
    risposta nei dati e la inventa.
    """
    from datetime import date, timedelta

    from app.db.models import SleepRecord

    for i in range(14):
        db.add(SleepRecord(user_id=user.id, day=date.today() - timedelta(days=i),
                           sleep_score=70, total_sleep_sec=7.5 * 3600))
    db.commit()

    text = briefing.build(db, user)

    assert "Sonno (ore) 7,5 h" in text
    assert "Sonno h" in text  # e anche nella serie di quattordici giorni


def test_the_series_says_where_to_read_the_unit(db, user):
    _seed_nights(db, user, [70] * 14)

    text = briefing.build(db, user)

    assert "l'unità è nel nome della riga" in text
    assert "Qualità /100" in text
    assert "Battery /100" in text


# ============================================================================
# I confronti già calcolati
# ============================================================================
# Un modello che riceve solo serie di numeri o dice l'ovvio o si inventa un
# legame che nei dati non c'è. I confronti si calcolano qui e gli arrivano
# fatti: è la differenza fra un insight e una didascalia.

def test_week_over_week_is_computed_for_the_model(db, user):
    from app.db.models import DailyWellness

    today = date.today()
    for i in range(14):
        # Prima settimana 6 ore, seconda 8: un cambiamento che si deve vedere.
        ore = 8 if i < 7 else 6
        db.add(SleepRecord(user_id=user.id, day=today - timedelta(days=i),
                           sleep_score=70, total_sleep_sec=ore * 3600))
        db.add(DailyWellness(user_id=user.id, day=today - timedelta(days=i),
                             resting_hr=55, body_battery_high=80))
    db.commit()

    text = briefing.build(db, user)

    assert "CONFRONTI GIÀ CALCOLATI" in text
    assert "Ore di sonno: 8,0 h questa settimana contro 6,0 h la precedente" in text


def test_the_model_is_told_not_to_eyeball_the_series(db, user):
    _seed_nights(db, user, [70] * 14)

    assert "non ricavarli a occhio" in briefing.build(db, user)


def test_a_comparison_needs_enough_days_on_both_sides(db, user):
    """Due notti contro tre non sono una tendenza, e presentarle come tale è
    peggio che tacere."""
    _seed_nights(db, user, [70] * 4)

    text = briefing.build(db, user)
    assert "questa settimana contro" not in text


def test_sleep_after_training_is_compared_when_there_is_enough(db, user):
    """La relazione che nessuno guarda da solo e che spiega più di tutte."""
    from app.db.models import Activity

    today = date.today()
    for i in range(20):
        # Un allenamento ogni due giorni, e il sonno peggiore il giorno dopo.
        allenato_ieri = (i + 1) % 2 == 0
        db.add(SleepRecord(user_id=user.id, day=today - timedelta(days=i),
                           sleep_score=50 if allenato_ieri else 85,
                           total_sleep_sec=7 * 3600))
        if i % 2 == 0:
            db.add(Activity(
                user_id=user.id, source="garmin", external_id=9000 + i,
                activity_type="running",
                start_time=datetime.combine(today - timedelta(days=i),
                                            datetime.min.time()),
                distance_m=10000, duration_sec=3000, avg_hr=150,
            ))
    db.commit()

    text = briefing.build(db, user)
    assert "il giorno dopo un allenamento" in text


# ============================================================================
# Le righe devono essere incolonnate davvero
# ============================================================================
#
# Il prompt dice al modello di leggerle in colonna. Prima le serie venivano da
# elenchi di misurazioni esistenti, quindi con tre notti non registrate la riga
# «Sonno» copriva diciassette giorni in quattordici celle mentre la riga
# «Carico» — costruita per data — ne copriva quattordici: le colonne
# raccontavano giorni diversi, e le correlazioni che il modello ne ricavava
# erano inventate.

def test_a_gap_becomes_a_column_not_a_shift():
    from app.ai.briefing import SERIES_DAYS, _series_line

    oggi = date(2026, 9, 13)
    con_buchi = {oggi - timedelta(days=i): 70 for i in range(14) if i not in (3, 4)}
    completa = {oggi - timedelta(days=i): 10 for i in range(14)}

    riga_buchi = _series_line("Sonno", con_buchi, oggi)
    riga_piena = _series_line("Carico", completa, oggi)

    assert riga_buchi.count("—") == 2
    # Stesso numero di celle: è questo che rende le colonne confrontabili.
    assert len(riga_buchi.split()) == len(riga_piena.split())
    assert len(riga_buchi.split()) == SERIES_DAYS + 2  # + etichetta + separatore


def test_the_last_cell_is_today():
    from app.ai.briefing import _series_line

    oggi = date(2026, 9, 13)
    serie = {oggi - timedelta(days=i): float(i) for i in range(14)}
    celle = _series_line("X", serie, oggi).split()[1:]
    celle = [c for c in celle if c != "·"]

    assert celle[-1] == "0"    # oggi
    assert celle[0] == "13"    # tredici giorni fa


# ============================================================================
# «Oggi» dev'essere oggi
# ============================================================================

def test_a_stale_value_says_how_old_it_is():
    from app.ai.briefing import build_baseline

    oggi = date(2026, 9, 13)
    # Nessuna misurazione nelle ultime due notti.
    serie = {oggi - timedelta(days=i): 60 + i for i in range(2, 20)}

    b = build_baseline("Qualità sonno", serie, oggi, higher_is_better=True, unit="/100")

    assert b.age_days == 2
    assert "2 giorni fa" in b.as_line()


def test_a_value_older_than_three_days_is_not_today_at_all():
    from app.ai.briefing import build_baseline

    oggi = date(2026, 9, 13)
    serie = {oggi - timedelta(days=i): 60 for i in range(5, 25)}

    b = build_baseline("Qualità sonno", serie, oggi, higher_is_better=True)

    assert b.today is None
    assert "non misurato oggi" in b.as_line()


def test_days_outside_counts_calendar_days_not_measurements():
    """Con una notte non registrata in mezzo, la serie fuori norma si ferma.

    Prima si camminava sulle misurazioni: tre valori storti con due notti vuote
    in mezzo diventavano «fuori norma da 3 giorni» pur coprendone cinque, e su
    quel numero il prompt decide se parlare di giornata storta o di tendenza.
    """
    from app.ai.briefing import build_baseline

    oggi = date(2026, 9, 13)
    serie = {oggi - timedelta(days=i): 80 for i in range(4, 30)}  # il normale
    serie[oggi] = 30            # storto
    serie[oggi - timedelta(days=1)] = 30   # storto
    # il giorno -2 manca, il -3 pure

    b = build_baseline("Qualità sonno", serie, oggi, higher_is_better=True)

    assert b.days_outside == 2
