"""Il punteggio di prontezza.

Il principio che questi test presidiano: **un fattore senza dato non vale 65,
non vale niente**. Prima ogni misura mancante diventava un valore neutro, e
siccome Garmin per molti account non popola mai il carico, il 15% del punteggio
era una costante che si presentava come una misurazione.
"""
from app.ai.readiness import WEIGHTS, compute_readiness


def _base() -> dict:
    return {
        "sleep_score": 80, "hrv_status": "balanced", "body_battery_high": 90,
        "load_ratio": 1.0, "resting_hr_7d_avg": 50, "resting_hr_30d_avg": 53,
    }


def _factor(result, name: str):
    return next(f for f in result.breakdown if f.name == name)


# --------------------------- fasce ---------------------------

def test_high_readiness_band():
    r = compute_readiness(_base())
    assert r.score >= 80
    assert r.emoji == "🔥"
    assert r.label == "Pronto"
    assert len(r.breakdown) == len(WEIGHTS)


def test_low_readiness_band():
    snap = {"sleep_score": 30, "hrv_status": "unbalanced", "body_battery_high": 20,
            "load_ratio": 1.8, "resting_hr_7d_avg": 60, "resting_hr_30d_avg": 55}
    r = compute_readiness(snap)
    assert r.score < 50
    assert r.emoji in {"🔵", "❌"}


def test_all_missing_returns_none_score():
    r = compute_readiness({})
    assert r.score is None
    assert r.label == "Dati insufficienti"
    assert r.measured_factors == []
    assert len(r.missing_factors) == len(WEIGHTS)


# --------------------------- dati mancanti ---------------------------

def test_a_missing_factor_stays_missing():
    """Il bug storico: il carico assente diventava un 65 indistinguibile da una misura."""
    snap = dict(_base())
    snap["load_ratio"] = None

    load = _factor(compute_readiness(snap), "Carico")

    assert load.value is None
    assert not load.is_measured
    assert load.display == "—"
    assert load.color == "muted"


def test_missing_hrv_stays_missing():
    snap = dict(_base())
    snap["hrv_status"] = None
    assert _factor(compute_readiness(snap), "HRV").value is None


def test_the_weights_redistribute_over_what_is_there():
    """Togliere un fattore non deve trascinare il punteggio verso la media.

    Con tutti i fattori alti, togliere il carico deve lasciare il punteggio
    alto — non abbassarlo di quindici punti verso un neutro inventato.
    """
    full = compute_readiness(_base())
    without_load = compute_readiness({**_base(), "load_ratio": None})

    assert abs(without_load.score - full.score) <= 6


def test_a_bad_missing_factor_does_not_flatter_the_score():
    """Simmetrico: con tutto basso, l'assenza non deve alzare il punteggio."""
    low = {"sleep_score": 25, "hrv_status": "unbalanced", "body_battery_high": 18,
           "load_ratio": 1.9, "resting_hr_7d_avg": 62, "resting_hr_30d_avg": 55}

    full = compute_readiness(low)
    without_load = compute_readiness({**low, "load_ratio": None})

    assert abs(without_load.score - full.score) <= 8


def test_a_single_measured_factor_is_not_enough():
    """Con solo la FC a riposo il punteggio non è fondato: meglio non darlo."""
    r = compute_readiness({"resting_hr_7d_avg": 50, "resting_hr_30d_avg": 53})
    assert r.score is None
    assert r.label == "Dati insufficienti"


def test_sleep_alone_is_not_enough():
    """Una notte buona non è una giornata pronta: serve almeno un secondo segnale."""
    assert compute_readiness({"sleep_score": 82}).score is None


def test_sleep_and_body_battery_together_are_enough():
    r = compute_readiness({"sleep_score": 82, "body_battery_high": 88})

    assert r.score is not None
    assert len(r.measured_factors) == 2
    assert len(r.missing_factors) == len(WEIGHTS) - 2


def test_the_weights_still_add_up_to_one():
    assert sum(WEIGHTS.values()) == 1.0


# --------------------------- HRV ---------------------------

def test_unbalanced_hrv_is_penalised_not_rewarded():
    """"unbalanced" contiene "balanc": l'ordine dei controlli conta."""
    from app.ai.readiness import _hrv_from_status

    assert _hrv_from_status("UNBALANCED") == 40
    assert _hrv_from_status("BALANCED") == 100
    assert _hrv_from_status("LOW") == 40
    assert _hrv_from_status("POOR") == 40
    assert _hrv_from_status(None) is None
    assert _hrv_from_status("SCONOSCIUTO") == 65


def test_unbalanced_hrv_lowers_the_score():
    base = {"sleep_score": 40, "body_battery_high": 25, "load_ratio": 1.6,
            "resting_hr_7d_avg": 60, "resting_hr_30d_avg": 55}
    balanced = compute_readiness({**base, "hrv_status": "BALANCED"})
    unbalanced = compute_readiness({**base, "hrv_status": "UNBALANCED"})

    assert unbalanced.score < balanced.score
    assert unbalanced.label == "Stanco"


# --------------------------- l'età del dato ---------------------------
#
# Il bug: `exact_latest` teneva un valore solo se la riga era di oggi. Chi
# apriva l'app prima della sincronizzazione del mattino — o dopo una notte in
# cui era fallita — si vedeva «Dati insufficienti» sulla schermata principale,
# con due mesi di storia alle spalle.

def test_yesterdays_data_still_counts_but_less():
    from app.ai.readiness import FRESHNESS

    fresco = compute_readiness({**_base(), "ages": {"sleep": 0, "battery": 0}})
    ieri = compute_readiness({**_base(), "ages": {"sleep": 1, "battery": 1}})

    assert ieri.score is not None
    assert ieri.covered_weight < fresco.covered_weight
    assert FRESHNESS[1] < FRESHNESS[0]


def test_data_older_than_two_days_stops_counting():
    r = compute_readiness({
        "sleep_score": 80, "body_battery_high": 90,
        "ages": {"sleep": 5, "battery": 5},
    })
    assert r.score is None
    sonno = _factor(r, "Sonno")
    # Non si mostra un valore che non sta contribuendo: verrebbe letto come la
    # spiegazione di un punteggio a cui non ha partecipato.
    assert sonno.value is None


def test_a_stale_factor_says_how_old_it_is():
    r = compute_readiness({**_base(), "ages": {"sleep": 1}})
    sonno = _factor(r, "Sonno")

    assert sonno.age_days == 1
    assert sonno.is_stale
    assert sonno.freshness_note == "dato di ieri"


# --------------------------- percentili personali ---------------------------

def test_the_same_score_reads_differently_for_two_athletes():
    """Il punto di tutta la personalizzazione, in un test solo.

    72 per chi dorme abitualmente sull'85 è una brutta notte; per chi sta sui
    60 è la migliore del mese. Prima contribuivano identici.
    """
    from app.analysis import baseline

    dormiglione = baseline.of([84, 86, 85, 83, 87, 85, 84, 86, 85, 88, 84, 86])
    insonne = baseline.of([58, 62, 60, 59, 61, 57, 63, 60, 58, 62, 59, 61])

    alto = compute_readiness({
        "sleep_score": 72, "body_battery_high": 70,
        "distributions": {"sleep": dormiglione},
    })
    basso = compute_readiness({
        "sleep_score": 72, "body_battery_high": 70,
        "distributions": {"sleep": insonne},
    })

    assert _factor(alto, "Sonno").value < _factor(basso, "Sonno").value
    assert _factor(alto, "Sonno").color == "red"
    assert _factor(basso, "Sonno").color == "green"


def test_a_short_history_keeps_the_absolute_reading():
    """Un percentile su quattro giornate descrive il campione, non la persona."""
    from app.analysis import baseline

    poche = baseline.of([80, 82, 79])
    r = compute_readiness({
        "sleep_score": 82, "body_battery_high": 88,
        "distributions": {"sleep": poche},
    })
    assert not poche.usable
    assert _factor(r, "Sonno").value == 82
    assert r.personalised is False


def test_resting_heart_rate_is_inverted():
    """Più bassa è meglio: la scala dei fattori punta sempre nella stessa direzione."""
    from app.analysis import baseline

    storia = baseline.of(
        [52, 54, 53, 55, 52, 56, 53, 54, 52, 55, 53, 54], higher_is_better=False
    )
    bassa = compute_readiness({
        "resting_hr_latest": 48, "sleep_score": 70,
        "distributions": {"rhr": storia},
    })
    alta = compute_readiness({
        "resting_hr_latest": 60, "sleep_score": 70,
        "distributions": {"rhr": storia},
    })

    assert _factor(bassa, "FC riposo").value > _factor(alta, "FC riposo").value


# --------------------------- il soggettivo ---------------------------

def test_the_morning_checkin_alone_is_not_enough():
    r = compute_readiness({"checkin": {"energy": 4, "legs": 4, "mood": 4}})
    assert r.score is None


def test_checkin_plus_load_and_form_are_enough_without_any_sensor():
    """È il caso di chi usa Strava: niente di notturno, e un punteggio comunque.

    Prima erano tre fattori su cinque irrimediabilmente vuoti, quindi il peso
    coperto non arrivava mai alla soglia e la prontezza non esisteva per
    quell'atleta — per sempre, non «finché non sincronizza».
    """
    r = compute_readiness({
        "checkin": {"energy": 4, "legs": 4, "mood": 5, "sleep_quality": 4},
        "load_ratio": 1.0,
        "tsb": 3,
    })
    assert r.score is not None
    assert {f.name for f in r.measured_factors} == {"Come stai", "Carico", "Forma"}


def test_the_checkin_maps_the_ends_of_the_scale():
    from app.ai.readiness import _subjective_factor

    assert _subjective_factor({"energy": 1, "legs": 1, "mood": 1}) == 0
    assert _subjective_factor({"energy": 3, "legs": 3, "mood": 3}) == 50
    assert _subjective_factor({"energy": 5, "legs": 5, "mood": 5}) == 100
    assert _subjective_factor(None) is None
    assert _subjective_factor({}) is None


def test_a_partial_checkin_still_counts():
    """Chi compila solo «gambe» dà comunque un'informazione."""
    from app.ai.readiness import _subjective_factor

    assert _subjective_factor({"legs": 5}) == 100
    assert _subjective_factor({"legs": 1, "energy": None}) == 0


def test_missing_everything_suggests_the_checkin_not_a_sync():
    """Per chi usa Strava «sincronizza» sarebbe mandarlo a sbattere."""
    r = compute_readiness({})
    assert "domande" in r.recommendation
    assert "sincronizz" not in r.recommendation.lower()


# --------------------------- la forma ---------------------------

def test_form_enters_the_score():
    from app.ai.readiness import _form_factor

    assert _form_factor(10) == 100        # fresco e carico
    assert _form_factor(-40) == 30        # in buca
    assert _form_factor(None) is None
    assert _form_factor(-40) < _form_factor(0)
