"""La lettura di ogni pagina: verdetto, evidenza, cosa fare.

Ogni funzione `read_*` prende le serie già caricate dalle query e restituisce
una `PageReading`. La struttura è sempre la stessa, perché è così che una
persona competente commenta dei dati:

    verdetto   — la frase che riassume la situazione
    evidenza   — i numeri che la sostengono
    azione     — la cosa concreta da fare

Più delle note brevi (`MetricNote`) da mettere accanto ai singoli grafici.

Tutto deterministico. Le soglie sono raccolte in cima a ogni sezione perché
sono la parte che si vorrà tarare con l'esperienza.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.insights import stats as st

# Dati sotto questa copertura: si dice che mancano, non si inventa un trend.
MIN_COVERAGE = 0.4


@dataclass
class MetricNote:
    """Una riga di commento accanto a un grafico."""

    metric: str  # a quale grafico si riferisce
    text: str
    tone: str = "neutral"  # good | warn | bad | neutral


@dataclass
class PageReading:
    """L'interpretazione di una pagina."""

    verdict: str
    evidence: str = ""
    action: str = ""
    tone: str = "neutral"
    notes: list[MetricNote] = field(default_factory=list)

    @property
    def has_content(self) -> bool:
        return bool(self.verdict)


def _sentence(parts: list[str]) -> str:
    """Unisce i pezzi in una frase senza rovinare sigle come FC o VO₂max."""
    if not parts:
        return ""
    joined = ", ".join(parts)
    return joined[0].upper() + joined[1:] + "."


def _no_data(what: str) -> PageReading:
    """`what` va scritto come completamento di «dati su…» (es. «sul sonno»)."""
    return PageReading(
        verdict=f"Non ho ancora abbastanza dati {what}.",
        evidence="Servono almeno qualche giorno di misurazioni per dire qualcosa di utile.",
        action="Premi Sincronizza e torna fra un paio di giorni.",
        tone="neutral",
    )


# ============================================================================
# Sonno
# ============================================================================

SLEEP_TARGET_HOURS = 7.5
SLEEP_SHORT_HOURS = 6.5
SLEEP_GOOD_SCORE = 75
SLEEP_POOR_SCORE = 60
DEEP_PCT_LOW = 15
REM_PCT_LOW = 18
IRREGULAR_STDEV_MIN = 65  # minuti di oscillazione: sopra, il sonno è irregolare


def read_sleep(rows: list) -> PageReading:
    """Interpreta le ultime notti: quantità, qualità, regolarità."""
    durations = [r.total_sleep_sec for r in rows]
    scores = [r.sleep_score for r in rows]
    if st.coverage(durations) < MIN_COVERAGE and st.coverage(scores) < MIN_COVERAGE:
        return _no_data("sul sonno")

    recent_dur, earlier_dur = st.split(durations, 7)
    recent_scores, earlier_scores = st.split(scores, 7)

    hours = (st.mean(recent_dur) or 0) / 3600
    score = st.mean(recent_scores)
    stdev_min = (st.spread(recent_dur) or 0) / 60

    deep_pct = _phase_pct(rows[-7:], "deep_sleep_sec")
    rem_pct = _phase_pct(rows[-7:], "rem_sleep_sec")

    notes: list[MetricNote] = []

    # --- note per grafico ---
    score_delta = st.delta(recent_scores, earlier_scores)
    if score_delta is not None and abs(score_delta) >= 4:
        better = score_delta > 0
        notes.append(MetricNote(
            "score",
            f"Score {'salito' if better else 'sceso'} di {abs(round(score_delta))} punti "
            "rispetto alle settimane precedenti.",
            "good" if better else "warn",
        ))

    if deep_pct is not None:
        notes.append(MetricNote(
            "phases",
            f"Sonno profondo al {round(deep_pct)}%"
            + (". Sotto il 15% il recupero fisico è incompleto."
               if deep_pct < DEEP_PCT_LOW else ", nella norma."),
            "warn" if deep_pct < DEEP_PCT_LOW else "good",
        ))

    if rem_pct is not None and rem_pct < REM_PCT_LOW:
        notes.append(MetricNote(
            "phases",
            f"REM al {round(rem_pct)}%: è la fase che consolida memoria e umore. "
            "Spesso cala con alcol la sera o orari irregolari.",
            "warn",
        ))

    if stdev_min >= IRREGULAR_STDEV_MIN:
        notes.append(MetricNote(
            "duration",
            f"Le tue notti oscillano di ±{round(stdev_min)} minuti: il corpo lavora "
            "meglio con orari costanti che con la media giusta.",
            "warn",
        ))

    # --- verdetto: quantità × qualità × regolarità ---
    quantity_ok = hours >= SLEEP_SHORT_HOURS
    quality_ok = score is not None and score >= SLEEP_GOOD_SCORE
    regular = stdev_min < IRREGULAR_STDEV_MIN

    evidence = (
        f"{st.fmt_duration(st.mean(recent_dur))} di media sulle ultime "
        f"{len(st.clean(recent_dur))} notti"
        + (f", score {round(score)}." if score is not None else ".")
    )

    if not quantity_ok and quality_ok:
        return PageReading(
            "Dormi bene, ma poco.",
            evidence + " La qualità c'è, manca la quantità.",
            f"Anticipa di {round((SLEEP_TARGET_HOURS - hours) * 60)} minuti l'ora in cui "
            "vai a letto: è la leva più semplice che hai.",
            "warn", notes,
        )
    if quantity_ok and not quality_ok:
        return PageReading(
            "Stai a letto abbastanza, ma il sonno non ristora.",
            evidence + " Il tempo c'è, la qualità no.",
            "Guarda cosa succede nelle tre ore prima di dormire: cena pesante, "
            "allenamento tardi e alcol sono le cause più frequenti.",
            "warn", notes,
        )
    if not quantity_ok and not quality_ok:
        return PageReading(
            "Il sonno è il tuo anello debole.",
            evidence + " Poco e poco profondo.",
            "Prima di cambiare l'allenamento, sistema il sonno: è da lì che passa "
            "tutto il resto.",
            "bad", notes,
        )
    if not regular:
        return PageReading(
            "Dormi bene, ma a orari irregolari.",
            evidence + f" Con oscillazioni di ±{round(stdev_min)} minuti fra una notte e l'altra.",
            "Prova a fissare l'ora della sveglia, anche nel fine settimana: "
            "è quella che stabilizza tutto il ritmo.",
            "warn", notes,
        )
    return PageReading(
        "Il sonno è in ordine.",
        evidence + " Quantità, qualità e regolarità sono a posto.",
        "Non toccare niente: è la base su cui puoi permetterti di alzare il carico.",
        "good", notes,
    )


def _phase_pct(rows: list, field_name: str) -> float | None:
    """Percentuale media di una fase sul totale dormito."""
    pairs = [
        (getattr(r, field_name), r.total_sleep_sec)
        for r in rows
        if getattr(r, field_name) and r.total_sleep_sec
    ]
    if not pairs:
        return None
    return 100 * sum(p / t for p, t in pairs) / len(pairs)


# ============================================================================
# Salute
# ============================================================================

RHR_MEANINGFUL_DELTA = 2.0
BATTERY_FULL_RECHARGE = 80
BATTERY_POOR_RECHARGE = 60
STRESS_HIGH = 45
STEPS_SEDENTARY = 6000


def read_health(rows: list) -> PageReading:
    """FC a riposo, Body Battery e stress: come il corpo regge il carico totale."""
    rhr = [r.resting_hr for r in rows]
    battery_high = [r.body_battery_high for r in rows]
    stress = [r.avg_stress for r in rows]
    steps = [r.total_steps for r in rows]

    if st.coverage(rhr) < MIN_COVERAGE and st.coverage(battery_high) < MIN_COVERAGE:
        return _no_data("sul tuo benessere quotidiano")

    recent_rhr, earlier_rhr = st.split(rhr, 7)
    rhr_delta = st.delta(recent_rhr, earlier_rhr)
    rhr_now = st.mean(recent_rhr)

    battery_mean = st.mean(st.split(battery_high, 7)[0])
    stress_mean = st.mean(st.split(stress, 7)[0])
    steps_mean = st.mean(st.split(steps, 7)[0])

    notes: list[MetricNote] = []

    if rhr_delta is not None and abs(rhr_delta) >= RHR_MEANINGFUL_DELTA:
        falling = rhr_delta < 0
        notes.append(MetricNote(
            "hr",
            f"FC a riposo {'scesa' if falling else 'salita'} di "
            f"{st.fmt(abs(rhr_delta), 1)} bpm rispetto alle settimane prima. "
            + ("È il segnale più affidabile che il cuore si sta adattando."
               if falling else
               "Di solito significa fatica accumulata, stress o sonno insufficiente."),
            "good" if falling else "warn",
        ))

    if battery_mean is not None:
        if battery_mean < BATTERY_POOR_RECHARGE:
            notes.append(MetricNote(
                "battery",
                f"Body Battery si ricarica in media solo fino a {round(battery_mean)}. "
                "Il corpo non chiude la giornata in pari: il debito si accumula.",
                "bad",
            ))
        elif battery_mean < BATTERY_FULL_RECHARGE:
            notes.append(MetricNote(
                "battery",
                f"Ricarica notturna media {round(battery_mean)}: parziale. "
                "Con questi valori puoi allenarti, ma non accumulare altro stress.",
                "warn",
            ))
        else:
            notes.append(MetricNote(
                "battery",
                f"Ricarica notturna piena ({round(battery_mean)}): il recupero funziona.",
                "good",
            ))

    if stress_mean is not None and stress_mean >= STRESS_HIGH:
        notes.append(MetricNote(
            "stress",
            f"Stress medio {round(stress_mean)}: alto. Garmin lo misura dall'HRV, "
            "quindi include il carico mentale, non solo l'allenamento.",
            "warn",
        ))

    if steps_mean is not None and steps_mean < STEPS_SEDENTARY:
        notes.append(MetricNote(
            "steps",
            f"{st.fmt(steps_mean)} passi al giorno fuori dagli allenamenti: "
            "il movimento di base incide sul recupero più di quanto sembri.",
            "warn",
        ))

    # --- verdetto ---
    evidence_parts = []
    if rhr_now is not None:
        evidence_parts.append(f"FC a riposo {round(rhr_now)} bpm")
    if battery_mean is not None:
        evidence_parts.append(f"ricarica notturna {round(battery_mean)}")
    if stress_mean is not None:
        evidence_parts.append(f"stress {round(stress_mean)}")
    evidence = _sentence(evidence_parts)

    recovering_badly = battery_mean is not None and battery_mean < BATTERY_POOR_RECHARGE
    rhr_rising = rhr_delta is not None and rhr_delta >= RHR_MEANINGFUL_DELTA
    rhr_falling = rhr_delta is not None and rhr_delta <= -RHR_MEANINGFUL_DELTA

    if rhr_rising and recovering_badly:
        return PageReading(
            "Stai accumulando fatica.",
            evidence + " Due segnali indipendenti che puntano nella stessa direzione.",
            "Prenditi due giorni facili veri. Non è pigrizia: è la condizione per "
            "cui l'allenamento della settimana prossima funziona.",
            "bad", notes,
        )
    if rhr_rising:
        return PageReading(
            "Il cuore lavora più del solito a riposo.",
            evidence + " La FC a riposo è salita.",
            "Se non hai cambiato carico, guarda sonno e stress: rispondono prima "
            "loro dell'allenamento.",
            "warn", notes,
        )
    if rhr_falling:
        return PageReading(
            "Il corpo si sta adattando bene.",
            evidence + " La FC a riposo sta scendendo.",
            "È il momento in cui alzare il carico rende: hai margine.",
            "good", notes,
        )
    if recovering_badly:
        return PageReading(
            "Il recupero notturno non chiude.",
            evidence + " Il Body Battery non torna su.",
            "Guarda cosa c'è oltre l'allenamento: lavoro, sonno, alcol. "
            "Il corpo non distingue le fonti di stress.",
            "warn", notes,
        )
    return PageReading(
        "I parametri di base sono stabili.",
        evidence + " Niente segnali di allarme.",
        "Situazione da mantenere: è la condizione in cui il lavoro fatto si consolida.",
        "good", notes,
    )


# ============================================================================
# Performance
# ============================================================================

VO2MAX_MEANINGFUL = 0.5
LOAD_RATIO_HIGH = 1.4
LOAD_RATIO_LOW = 0.8


def read_performance(rows: list) -> PageReading:
    """VO2max, carico e HRV: la fitness sta salendo e a che prezzo."""
    vo2 = [r.vo2max for r in rows]
    load = [r.training_load for r in rows]
    hrv = [r.hrv_weekly_avg for r in rows]

    if st.coverage(vo2) < MIN_COVERAGE and st.coverage(load) < MIN_COVERAGE:
        return _no_data("sulle metriche di performance")

    vo2_now = st.latest(vo2)
    recent_vo2, earlier_vo2 = st.split(vo2, 14)
    vo2_delta = st.delta(recent_vo2, earlier_vo2)

    acute = st.mean(st.split(load, 7)[0])
    chronic = st.mean(load)
    ratio = acute / chronic if acute is not None and chronic else None

    status = next((r.training_status for r in reversed(rows) if r.training_status), None)
    hrv_status = next((r.hrv_status for r in reversed(rows) if r.hrv_status), None)

    notes: list[MetricNote] = []

    if vo2_delta is not None and abs(vo2_delta) >= VO2MAX_MEANINGFUL:
        rising = vo2_delta > 0
        notes.append(MetricNote(
            "vo2max",
            f"VO₂max {'+' if rising else ''}{st.fmt(vo2_delta, 1)} nelle ultime due "
            "settimane. " + ("Il motore aerobico sta crescendo." if rising else
                             "Un calo può dipendere da fatica o da poche uscite lunghe."),
            "good" if rising else "warn",
        ))

    if ratio is not None:
        if ratio >= LOAD_RATIO_HIGH:
            notes.append(MetricNote(
                "load",
                f"Carico acuto {st.fmt(ratio, 1)}× il cronico: sopra 1,4 la "
                "probabilità di infortunio sale nettamente.",
                "bad",
            ))
        elif ratio <= LOAD_RATIO_LOW:
            notes.append(MetricNote(
                "load",
                f"Carico acuto {st.fmt(ratio, 1)}× il cronico: stai scaricando. "
                "Giusto in fase di recupero, poco se stai costruendo.",
                "warn",
            ))
        else:
            notes.append(MetricNote(
                "load",
                f"Carico acuto {st.fmt(ratio, 1)}× il cronico: nella fascia in cui "
                "si costruisce senza rompersi.",
                "good",
            ))

    if hrv_status:
        low = "unbalanc" in hrv_status.lower() or "low" in hrv_status.lower()
        notes.append(MetricNote(
            "hrv",
            f"HRV: {hrv_status}. " + ("Il sistema nervoso non è tornato in equilibrio."
                                      if low else "Sistema nervoso in equilibrio."),
            "warn" if low else "good",
        ))

    # --- verdetto ---
    evidence_parts = []
    if vo2_now is not None:
        evidence_parts.append(f"VO₂max {st.fmt(vo2_now, 1)}")
    if ratio is not None:
        evidence_parts.append(f"rapporto di carico {st.fmt(ratio, 1)}")
    if status:
        evidence_parts.append(f"Garmin dice «{status}»")
    evidence = _sentence(evidence_parts)

    overloaded = ratio is not None and ratio >= LOAD_RATIO_HIGH
    improving = vo2_delta is not None and vo2_delta >= VO2MAX_MEANINGFUL
    declining = vo2_delta is not None and vo2_delta <= -VO2MAX_MEANINGFUL

    if overloaded and improving:
        return PageReading(
            "Stai crescendo, ma su un carico che non regge a lungo.",
            evidence + " I risultati arrivano, il rapporto di carico è oltre la soglia.",
            "Consolida: tieni questo volume per una settimana invece di alzarlo ancora. "
            "L'adattamento avviene nel recupero, non nello sforzo.",
            "warn", notes,
        )
    if overloaded:
        return PageReading(
            "Il carico è sopra quello che il tuo corpo ha costruito.",
            evidence + " Senza un guadagno di fitness che lo giustifichi.",
            "Riduci il volume del 20% questa settimana. Stai pagando senza incassare.",
            "bad", notes,
        )
    if improving:
        return PageReading(
            "La fitness sta salendo.",
            evidence + " Con un carico sostenibile.",
            "Continua così: quando il VO₂max sale a carico stabile, vuol dire che "
            "l'allenamento è tarato bene.",
            "good", notes,
        )
    if declining:
        return PageReading(
            "La fitness sta calando.",
            evidence + " Il VO₂max è in discesa.",
            "Se non sei in scarico programmato, ti manca stimolo: aggiungi una "
            "sessione di qualità a settimana.",
            "warn", notes,
        )
    return PageReading(
        "Fitness stabile.",
        evidence + " Nessun movimento significativo.",
        "Per farla salire serve un cambiamento: più volume o più intensità, "
        "non entrambi insieme.",
        "neutral", notes,
    )


# ============================================================================
# Corpo
# ============================================================================

WEIGHT_MEANINGFUL_KG = 0.4
SAFE_LOSS_KG_PER_WEEK = 0.7
MUSCLE_LOSS_ALERT_KG = 0.3


def read_body(rows: list) -> PageReading:
    """Peso e composizione: la direzione, il ritmo e cosa stai perdendo."""
    weights = [r.weight_g for r in rows]
    if st.coverage(weights) < MIN_COVERAGE or len(st.clean(weights)) < 3:
        return PageReading(
            "Servono più misurazioni per leggere un andamento.",
            "Il peso oscilla di 1-2 kg al giorno per acqua e alimentazione: "
            "solo la media su più giorni dice qualcosa.",
            "Pesati con una certa regolarità, sempre nelle stesse condizioni.",
            "neutral",
        )

    kg = [w / 1000 for w in st.clean(weights)]
    weight_now = kg[-1]
    per_point = st.slope([w / 1000 if w else None for w in weights])
    days_span = max(len(weights) - 1, 1)
    total_change = st.delta(kg[-7:], kg[:-7]) if len(kg) > 7 else None

    # La pendenza è per punto: la riporto a settimana usando l'ampiezza reale.
    per_week = per_point * 7 if per_point is not None else None

    fat = [r.body_fat_pct for r in rows]
    muscle = [r.muscle_mass_g for r in rows]
    fat_delta = st.delta(st.split(fat, 7)[0], st.split(fat, 7)[1])
    muscle_delta_g = st.delta(st.split(muscle, 7)[0], st.split(muscle, 7)[1])
    muscle_delta = muscle_delta_g / 1000 if muscle_delta_g is not None else None

    notes: list[MetricNote] = []

    if per_week is not None and abs(per_week) >= 0.05:
        losing = per_week < 0
        notes.append(MetricNote(
            "weight",
            f"Tendenza: {st.fmt(abs(per_week), 2)} kg a settimana "
            f"{'in calo' if losing else 'in salita'}.",
            "neutral",
        ))
        if losing and abs(per_week) > SAFE_LOSS_KG_PER_WEEK:
            notes.append(MetricNote(
                "weight",
                f"Oltre {st.fmt(SAFE_LOSS_KG_PER_WEEK, 1)} kg a settimana si perde "
                "anche massa magra, non solo grasso.",
                "warn",
            ))

    if fat_delta is not None and abs(fat_delta) >= 0.3:
        notes.append(MetricNote(
            "composition",
            f"Massa grassa {st.fmt(fat_delta, 1)} punti percentuali "
            f"{'in meno' if fat_delta < 0 else 'in più'}.",
            "good" if fat_delta < 0 else "warn",
        ))

    if muscle_delta is not None and muscle_delta <= -MUSCLE_LOSS_ALERT_KG:
        notes.append(MetricNote(
            "composition",
            f"Massa muscolare {st.fmt(abs(muscle_delta), 1)} kg in meno: "
            "è la parte che vuoi tenere.",
            "bad",
        ))

    # --- verdetto: il caso interessante è perdere peso perdendo muscolo ---
    evidence = f"{st.fmt(weight_now, 1)} kg"
    if per_week is not None and abs(per_week) >= 0.05:
        evidence += f", {st.fmt(abs(per_week), 2)} kg a settimana " \
                    f"{'in calo' if per_week < 0 else 'in salita'}"
    evidence += "."

    losing_muscle = muscle_delta is not None and muscle_delta <= -MUSCLE_LOSS_ALERT_KG
    losing_weight = per_week is not None and per_week <= -0.05
    too_fast = per_week is not None and per_week < -SAFE_LOSS_KG_PER_WEEK

    if losing_weight and losing_muscle:
        return PageReading(
            "Stai perdendo peso, ma anche muscolo.",
            evidence + " Il calo non è solo grasso.",
            "Alza le proteine e tieni il lavoro di forza: la bilancia scende comunque, "
            "ma scende la parte giusta.",
            "bad", notes,
        )
    if too_fast:
        return PageReading(
            "Stai calando troppo in fretta.",
            evidence + f" Oltre i {st.fmt(SAFE_LOSS_KG_PER_WEEK, 1)} kg a settimana "
                       "sostenibili.",
            "Riduci il deficit: a questo ritmo perdi anche prestazione, e il peso "
            "torna appena molli.",
            "warn", notes,
        )
    if losing_weight:
        return PageReading(
            "Il peso sta scendendo con un ritmo sostenibile.",
            evidence + " Nella fascia in cui si perde grasso e si tiene il muscolo.",
            "Continua così e ricontrolla fra due settimane: è il tempo minimo per "
            "distinguere un trend dal rumore.",
            "good", notes,
        )
    if per_week is not None and per_week >= 0.05:
        return PageReading(
            "Il peso sta salendo.",
            evidence,
            "Se non è voluto, il punto non è la bilancia ma cosa è cambiato: "
            "volume di allenamento, sonno o alimentazione.",
            "warn", notes,
        )
    return PageReading(
        "Peso stabile.",
        evidence + " Nessuna variazione significativa.",
        "La stabilità è un risultato se ci stai lavorando: significa che "
        "entrate e uscite sono in pari.",
        "neutral", notes,
    )


# ============================================================================
# Attività
# ============================================================================

EASY_HR_FRACTION = 0.72   # sotto il 72% della FC max osservata = facile
HARD_HR_FRACTION = 0.82   # sopra l'82% = duro
POLARIZED_EASY_TARGET = 75  # % di uscite facili in un allenamento ben distribuito


def read_activities(activities: list) -> PageReading:
    """Volume, costanza e distribuzione delle intensità."""
    if len(activities) < 3:
        return _no_data("sui tuoi allenamenti")

    with_time = [a for a in activities if a.start_time]
    if not with_time:
        return _no_data("sui tuoi allenamenti")

    total_km = sum((a.distance_m or 0) for a in with_time) / 1000
    total_hours = sum((a.duration_sec or 0) for a in with_time) / 3600
    span_days = max((max(a.start_time for a in with_time)
                     - min(a.start_time for a in with_time)).days, 1)
    weeks = max(span_days / 7, 1)
    km_per_week = total_km / weeks
    sessions_per_week = len(with_time) / weeks

    notes: list[MetricNote] = []

    # --- distribuzione delle intensità ---
    max_hr = max((a.max_hr for a in with_time if a.max_hr), default=None)
    easy = moderate = hard = 0
    classified = 0
    for activity in with_time:
        zone = _intensity(activity, max_hr)
        if zone is None:
            continue
        classified += 1
        if zone == "easy":
            easy += 1
        elif zone == "hard":
            hard += 1
        else:
            moderate += 1

    easy_pct = 100 * easy / classified if classified else None

    if easy_pct is not None and classified >= 5:
        if easy_pct < 50:
            notes.append(MetricNote(
                "distribution",
                f"Solo il {round(easy_pct)}% delle tue uscite è davvero facile. "
                "È l'errore più comune: correre a un'intensità media che affatica "
                "senza dare lo stimolo di una sessione dura.",
                "warn",
            ))
        elif easy_pct >= POLARIZED_EASY_TARGET:
            notes.append(MetricNote(
                "distribution",
                f"{round(easy_pct)}% di uscite facili: la distribuzione è quella "
                "giusta, facile davvero facile e duro davvero duro.",
                "good",
            ))
        else:
            notes.append(MetricNote(
                "distribution",
                f"{round(easy_pct)}% di uscite facili. Il riferimento è circa "
                f"{POLARIZED_EASY_TARGET}%: qualche uscita in più a ritmo blando "
                "renderebbe le sessioni dure più efficaci.",
                "neutral",
            ))

    if sessions_per_week:
        notes.append(MetricNote(
            "volume",
            f"{st.fmt(sessions_per_week, 1)} allenamenti a settimana, "
            f"{st.fmt(km_per_week, 1)} km e {st.fmt(total_hours / weeks, 1)} ore.",
            "neutral",
        ))

    longest = max((a.distance_m or 0) for a in with_time) / 1000
    if longest:
        notes.append(MetricNote(
            "volume",
            f"Uscita più lunga: {st.fmt(longest, 1)} km.",
            "neutral",
        ))

    # --- verdetto ---
    evidence = (
        f"{st.fmt(sessions_per_week, 1)} sessioni a settimana per "
        f"{st.fmt(km_per_week, 1)} km."
    )

    if sessions_per_week < 2:
        return PageReading(
            "Il volume è basso per costruire.",
            evidence + " Sotto le due sessioni a settimana i progressi non si accumulano.",
            "Tre uscite regolari valgono più di due lunghe e discontinue: "
            "la costanza conta più della singola seduta.",
            "warn", notes,
        )

    if easy_pct is not None and classified >= 5 and easy_pct < 50:
        return PageReading(
            "Corri quasi sempre alla stessa intensità media.",
            evidence + f" Solo il {round(easy_pct)}% delle uscite è in fascia facile.",
            "Rallenta le uscite facili al punto da poter parlare, e tieni l'intensità "
            "per una o due sessioni a settimana. Stessa fatica, più risultato.",
            "warn", notes,
        )
    if easy_pct is not None and easy_pct >= POLARIZED_EASY_TARGET:
        return PageReading(
            "Volume e distribuzione delle intensità sono impostati bene.",
            evidence + f" Con il {round(easy_pct)}% di lavoro facile.",
            "Su questa base puoi crescere: alza il volume del 10% a settimana, "
            "non di più.",
            "good", notes,
        )
    return PageReading(
        "Ti alleni con regolarità.",
        evidence,
        "Per migliorare, il prossimo passo è distinguere meglio le intensità: "
        "le uscite facili più lente, quelle dure più intense.",
        "neutral", notes,
    )


def _intensity(activity, max_hr: float | None) -> str | None:
    """Classifica un allenamento in facile / medio / duro.

    Preferisce la FC media rapportata alla massima osservata; se manca, usa il
    training effect aerobico che Garmin calcola.
    """
    if activity.avg_hr and max_hr:
        fraction = activity.avg_hr / max_hr
        if fraction < EASY_HR_FRACTION:
            return "easy"
        if fraction > HARD_HR_FRACTION:
            return "hard"
        return "moderate"

    if activity.aerobic_te:
        if activity.aerobic_te < 2.0:
            return "easy"
        if activity.aerobic_te >= 3.0:
            return "hard"
        return "moderate"

    return None
