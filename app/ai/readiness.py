"""Il punteggio di prontezza. Python puro, deterministico, zero AI.

Tre principi, ognuno nato da un difetto della versione precedente.

**Un fattore senza dato non vale 65, non vale niente.** Sostituire una misura
mancante con un valore neutro produceva una pastiglia «Carico 65» identica a
una misura vera. I pesi si ridistribuiscono sui fattori presenti, e
l'interfaccia può dire quanto il punteggio sa davvero.

**Un dato di ieri vale meno, ma vale.** Prima il valore contava solo se la
riga era esattamente di oggi, e il resto spariva: chi apriva l'app prima della
sincronizzazione delle 6:30 — o dopo una notte in cui la sync era fallita —
si trovava «Dati insufficienti» sulla schermata principale, pur avendo sei
notti di storia alle spalle. Adesso il peso decade con l'età del dato e si
azzera al terzo giorno.

**Un punteggio va letto contro la propria storia, non contro la popolazione.**
Il punteggio del sonno di Garmin è già una media di tutti: 72 per chi dorme
abitualmente 85 è una brutta notte, per chi sta sui 60 è la migliore del mese.
Trattarli uguale voleva dire non misurare la prontezza di nessuno dei due. Con
almeno dieci giorni di storia ogni fattore diventa il proprio rango percentile
(vedi `app/analysis/baseline.py`); sotto quella soglia si ricade sulla lettura
assoluta, perché un percentile su quattro giornate descrive il campione e non
la persona.

Si aggiungono due fattori che prima non c'erano:

- **la forma** (TSB), che si calcola dalle attività e quindi esiste per
  chiunque, anche per chi non ha niente di notturno;
- **il soggettivo**, cioè il check-in del mattino. In letteratura predice
  l'affaticamento meglio di HRV e frequenza a riposo, costa quindici secondi,
  ed è l'unico modo perché un atleta su Strava arrivi alla soglia di
  fondatezza invece di vedersi negare il punteggio per sempre.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.analysis.baseline import Distribution

WEIGHTS = {
    "sleep": 0.22,
    "hrv": 0.18,
    "battery": 0.15,
    "subjective": 0.15,
    "load": 0.12,
    "form": 0.10,
    "rhr": 0.08,
}

# Sotto questa quota di peso coperto da misure vere, il punteggio non è
# abbastanza fondato per essere mostrato come un numero.
#
# 0,35 non è un numero tondo scelto a caso: è quanto copre un atleta che non
# misura niente di notte ma fa il check-in del mattino (soggettivo 0,15 +
# carico 0,12 + forma 0,10 = 0,37). Chi usa Strava e risponde alle tre domande
# ottiene un punteggio; chi non risponde no, ed è la risposta giusta a
# entrambi.
MIN_COVERED_WEIGHT = 0.35

# Quanto conta un dato vecchio di N giorni. Al terzo giorno non conta più:
# la frequenza a riposo di tre notti fa non dice niente su stamattina.
FRESHNESS = {0: 1.0, 1: 0.6, 2: 0.3}

# Le quattro domande del check-in, tutte orientate «più alto è meglio».
CHECKIN_FIELDS = ("energy", "legs", "mood", "sleep_quality")
CHECKIN_SCALE = 5


@dataclass
class ReadinessFactor:
    name: str
    value: int | None
    color: str
    # Da quanti giorni è il dato. 0 = stamattina. `None` quando non c'è.
    age_days: int | None = None
    # Il peso con cui ha davvero contribuito, dopo il decadimento.
    weight: float = 0.0

    @property
    def is_measured(self) -> bool:
        return self.value is not None

    @property
    def display(self) -> str:
        return str(self.value) if self.value is not None else "—"

    @property
    def is_stale(self) -> bool:
        return bool(self.age_days)

    @property
    def freshness_note(self) -> str:
        """Come dirlo in interfaccia, senza far finta che sia di stamattina."""
        if not self.is_measured or not self.age_days:
            return ""
        return "dato di ieri" if self.age_days == 1 else f"dato di {self.age_days} giorni fa"


@dataclass
class ReadinessResult:
    score: int | None
    label: str
    emoji: str
    recommendation: str
    breakdown: list[ReadinessFactor]
    covered_weight: float = 0.0
    personalised: bool = False

    @property
    def measured_factors(self) -> list[ReadinessFactor]:
        return [f for f in self.breakdown if f.is_measured]

    @property
    def missing_factors(self) -> list[ReadinessFactor]:
        return [f for f in self.breakdown if not f.is_measured]

    @property
    def stale_factors(self) -> list[ReadinessFactor]:
        return [f for f in self.breakdown if f.is_stale]

    @property
    def confidence_pct(self) -> int:
        """Quanta parte del punteggio poggia su misure vere e fresche."""
        return round(100 * self.covered_weight)


def _color(v: int | None) -> str:
    if v is None:
        return "muted"
    if v >= 70:
        return "green"
    if v >= 45:
        return "amber"
    return "red"


def _clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def _freshness(age_days: int | None) -> float:
    """Quanto pesa ancora un dato di N giorni fa.

    Età ignota vuol dire **fresco**, non scaduto: chi costruisce uno snapshot
    a mano — i test, o un chiamante che ha solo i valori — non deve vedersi
    annullare i fattori per una chiave che non sapeva di dover riempire. Chi
    l'età ce l'ha la dichiara, e `coach_snapshot` la dichiara sempre.
    """
    if age_days is None:
        return 1.0
    return FRESHNESS.get(int(age_days), 0.0)


# ============================================================================
# I singoli fattori
# ============================================================================


def _scored(value: float | None, dist: Distribution | None,
            absolute) -> int | None:
    """Il percentile personale se c'è storia, altrimenti la lettura assoluta.

    `absolute` è la funzione da usare quando la storia non basta: sotto dieci
    giorni un rango percentile racconta il campione, non l'abitudine.
    """
    if value is None:
        return None
    if dist is not None and dist.usable:
        pct = dist.percentile_of(value)
        if pct is not None:
            return _clamp(pct)
    return absolute(value)


def _hrv_from_status(status: str | None) -> int | None:
    """Garmin usa BALANCED / UNBALANCED / LOW / POOR.

    Ricaduta per quando manca il valore numerico settimanale. I casi negativi
    vanno controllati per primi: "unbalanced" contiene "balanc", quindi
    l'ordine inverso premierebbe un HRV sbilanciato.
    """
    if not status:
        return None
    s = status.lower()
    if "unbalanc" in s or "low" in s or "poor" in s:
        return 40
    if "balanc" in s or "good" in s:
        return 100
    return 65


def _load_factor(ratio: float | None) -> int | None:
    """Il rapporto acuto/cronico calcolato in `app.analysis.load`.

    Resta assoluto e non percentile: il rapporto è **già** normalizzato sulla
    storia della persona per costruzione — è il suo carico diviso il suo
    carico abituale — e le soglie di 1,2 e 1,5 vengono dalla letteratura sugli
    infortuni, non dalla distribuzione di questo atleta.

    È `None` finché non esiste un'abitudine rispetto a cui misurare la
    settimana: in quel caso il carico non entra nel punteggio.
    """
    if ratio is None:
        return None
    if ratio > 1.5:
        return 30
    if ratio > 1.2:
        return 55
    if ratio < 0.8:
        return 70
    return 80


def _form_factor(tsb: float | None) -> int | None:
    """La forma del Performance Management Chart, letta come disponibilità.

    Nuovo rispetto a prima, e importante per una ragione di equità: si calcola
    dalle attività, quindi **esiste per chiunque**, anche per chi non ha nulla
    di notturno. Le fasce sono quelle d'uso comune fra allenatori.
    """
    if tsb is None:
        return None
    if tsb > 25:
        return 85   # freschissimo, ma si sta perdendo il lavoro fatto
    if tsb > 5:
        return 100  # fresco e carico: è la giornata delle sessioni chiave
    if tsb >= -10:
        return 80   # in equilibrio
    if tsb >= -30:
        return 55   # sotto carico: è dove si costruisce, ma si sente
    return 30       # in buca


def _subjective_factor(checkin: dict | None) -> int | None:
    """Le risposte del mattino, sulla stessa scala di tutto il resto.

    La media delle domande a cui ha risposto, non di tutte e quattro: chi
    compila solo «gambe» dà comunque un'informazione, e obbligarlo a compilare
    il resto per essere contato significherebbe non farlo compilare affatto.
    """
    if not checkin:
        return None
    answers = [
        checkin[f] for f in CHECKIN_FIELDS
        if checkin.get(f) is not None
    ]
    if not answers:
        return None
    # Da 1-5 a 0-100: 1 → 0, 3 → 50, 5 → 100.
    mean = sum(answers) / len(answers)
    return _clamp(100 * (mean - 1) / (CHECKIN_SCALE - 1))


def _band(score: int) -> tuple[str, str, str]:
    if score >= 80:
        return "🔥", "Pronto", "Allenamento intenso / sessione chiave"
    if score >= 65:
        return "✅", "Buono", "Allenamento moderato"
    if score >= 50:
        return "🟡", "Discreto", "Corsa easy / attività leggera"
    if score >= 35:
        return "🔵", "Stanco", "Recovery run o riposo attivo"
    return "❌", "Riposo", "Riposo completo"


# ============================================================================
# Il punteggio
# ============================================================================

NAMES = {
    "sleep": "Sonno", "hrv": "HRV", "battery": "Body Battery",
    "subjective": "Come stai", "load": "Carico", "form": "Forma",
    "rhr": "FC riposo",
}


def compute_readiness(snap: dict) -> ReadinessResult:
    """Il punteggio, dai valori già calcolati nello snapshot.

    Le chiavi `*_age` e `dist_*` sono facoltative: senza, il fattore vale come
    fosse di oggi e si legge in assoluto. Serve ai chiamanti che costruiscono
    uno snapshot a mano — i test, soprattutto — per non doverli conoscere.
    """
    dist = snap.get("distributions") or {}

    values: dict[str, int | None] = {
        "sleep": _scored(
            snap.get("sleep_score"), dist.get("sleep"), lambda v: _clamp(v)
        ),
        "hrv": _scored(
            snap.get("hrv_weekly_avg"), dist.get("hrv"),
            lambda _v: _hrv_from_status(snap.get("hrv_status")),
        ) if snap.get("hrv_weekly_avg") is not None
        else _hrv_from_status(snap.get("hrv_status")),
        "battery": _scored(
            snap.get("body_battery_high"), dist.get("battery"), lambda v: _clamp(v)
        ),
        "subjective": _subjective_factor(snap.get("checkin")),
        "load": _load_factor(snap.get("load_ratio")),
        "form": _form_factor(snap.get("tsb")),
        "rhr": _scored(
            snap.get("resting_hr_latest"), dist.get("rhr"),
            lambda _v: _rhr_versus_month(
                snap.get("resting_hr_7d_avg"), snap.get("resting_hr_30d_avg")
            ),
        ),
    }

    ages = snap.get("ages") or {}
    # Carico e forma sono medie mobili su settimane: non «invecchiano» come una
    # misura puntuale della notte, e un giorno in più o in meno non le sposta.
    always_fresh = {"load", "form"}

    breakdown: list[ReadinessFactor] = []
    total = 0.0
    covered = 0.0
    for key in ("sleep", "hrv", "battery", "subjective", "load", "form", "rhr"):
        value = values[key]
        age = None if key in always_fresh else ages.get(key)
        weight = WEIGHTS[key] * (1.0 if key in always_fresh else _freshness(age))

        if value is not None and weight > 0:
            total += value * weight
            covered += weight
        else:
            # Un dato troppo vecchio è un dato che non c'è: mostrarlo come
            # misurato mentre non conta nel punteggio sarebbe peggio che
            # tacerlo, perché l'atleta lo leggerebbe come una spiegazione.
            value = value if weight > 0 else None

        breakdown.append(
            ReadinessFactor(NAMES[key], value, _color(value), age, weight)
        )

    personalised = any(d.usable for d in dist.values() if d is not None)

    if covered < MIN_COVERED_WEIGHT:
        return ReadinessResult(
            None, "Dati insufficienti", "—",
            _missing_advice(breakdown),
            breakdown, covered, personalised,
        )

    score = _clamp(total / covered)
    emoji, label, rec = _band(score)
    return ReadinessResult(score, label, emoji, rec, breakdown, covered, personalised)


def _rhr_versus_month(rhr_7d: float | None, rhr_30d: float | None) -> int | None:
    """La settimana contro il mese, per chi non ha ancora abbastanza storia."""
    if rhr_7d is None or rhr_30d is None:
        return None
    if rhr_7d <= rhr_30d - 2:
        return 80
    if rhr_7d >= rhr_30d + 2:
        return 40
    return 65


def _missing_advice(breakdown: list[ReadinessFactor]) -> str:
    """Cosa manca, detto a chi può farci qualcosa.

    Niente riferimenti a Garmin: da quando le sorgenti sono due, per chi usa
    Strava sonno e HRV non arriveranno **mai**, e dirgli di sincronizzare
    sarebbe mandarlo a sbattere. Il check-in invece può farlo chiunque, ed è
    per questo che è la prima cosa che si suggerisce.
    """
    subjective = next((f for f in breakdown if f.name == NAMES["subjective"]), None)
    if subjective is not None and not subjective.is_measured:
        return (
            "Rispondi alle tre domande del mattino: bastano quelle e il carico "
            "per avere un punteggio."
        )
    return "Servono ancora qualche giorno di misurazioni."
