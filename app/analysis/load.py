"""Il carico di allenamento, calcolato invece che chiesto.

Tre passaggi, ognuno con una domanda precisa:

1. **Quanto è costata una singola seduta?** → `activity_load`
   Il metro migliore disponibile fra potenza, frequenza cardiaca e durata.

2. **Quanto sto accumulando?** → `pmc_series`
   Le due medie mobili esponenziali del Performance Management Chart:
   una lenta (fitness), una veloce (fatica). La differenza è la forma.

3. **Sto salendo troppo in fretta?** → `acwr`, `monotony`, `strain`
   Il rapporto fra la settimana appena fatta e il mese precedente, e quanto
   quel carico è distribuito o ammassato.

Le costanti sono in cima perché sono la parte che si vorrà tarare.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import pstdev

from app.analysis.profile import AthleteProfile

# --- Performance Management Chart -------------------------------------------
# Le due costanti di tempo classiche di Coggan: 42 giorni per la fitness,
# 7 per la fatica. La fatica sale e scende in fretta, la fitness no: è per
# questo che scaricare prima di una gara funziona.
CTL_DAYS = 42
ATL_DAYS = 7

# --- Rapporto carico acuto/cronico ------------------------------------------
#
# Le due finestre non si sovrappongono, ed è la differenza fra la versione
# «accoppiata» e quella scollegata. Nella prima — quella che c'era qui — la
# settimana appena fatta sta **dentro** il proprio denominatore: se raddoppi il
# carico, alzi anche la media con cui ti stai confrontando, e il rapporto
# cresce meno di quanto la salita meriti. Lolli e altri hanno mostrato che quel
# legame produce correlazioni spurie e smorza proprio il segnale che si vuole
# leggere. Qui la settimana acuta sono gli ultimi 7 giorni, la cronica le 3
# settimane **precedenti**.
ACUTE_DAYS = 7
CHRONIC_DAYS = 28
# I giorni che compongono la finestra cronica: dall'ottavo al ventottesimo.
CHRONIC_OFFSET = ACUTE_DAYS
CHRONIC_SPAN = CHRONIC_DAYS - ACUTE_DAYS
# Sopra 1,5 il rischio di infortunio cresce in modo netto in tutta la
# letteratura sul carico; sotto 0,8 si sta scaricando.
ACWR_DANGER = 1.5
ACWR_HIGH = 1.3
ACWR_LOW = 0.8

# Il rapporto ha senso solo se esiste un'abitudine rispetto a cui misurarsi.
# Chi si allena una volta ogni tre settimane, la volta che si allena ottiene un
# rapporto di 3 e l'app grida al sovrallenamento per una singola uscita: non è
# un rischio, è una divisione per quasi zero.
ACWR_MIN_TRAINING_DAYS = 8

# --- Monotonia di Foster ----------------------------------------------------
# Carico medio diviso la sua variabilità: alto = tutti i giorni uguali, che è
# il modo più efficace per accumulare fatica senza accumulare adattamento.
MONOTONY_HIGH = 2.0
# Con variabilità zero — sette giorni identici — il rapporto sarebbe infinito.
# È il caso *peggiore*, cioè esattamente quello che questa metrica esiste per
# segnalare, e prima veniva restituito come `None`: la settimana più monotona
# possibile era l'unica su cui l'app non diceva niente. Si satura invece di
# sparire.
MONOTONY_MAX = 10.0
# Con una o due sedute in sette giorni il rapporto esce lo stesso, ma non
# descrive un modo di distribuire il carico: descrive due punti.
MONOTONY_MIN_TRAINING_DAYS = 3

# --- Provenienza del carico di una seduta -----------------------------------
FROM_POWER = "potenza"
FROM_HR = "frequenza cardiaca"
FROM_RPE = "sforzo percepito"
FROM_DURATION = "durata"

# Fattore che riporta il TRIMP sulla scala del TSS, dove 100 = un'ora al
# massimo sforzo sostenibile. Serve solo perché i due numeri siano leggibili
# insieme: il rapporto fra le sedute non cambia.
TRIMP_TO_TSS = 100 / 167

# Carico attribuito a un'ora di attività di cui si conosce solo la durata.
# Volutamente basso: è un tappabuchi, non deve competere con una misura vera.
LOAD_PER_HOUR_UNKNOWN = 40.0

# sRPE di Foster: sforzo percepito (Borg CR10) × minuti. Il fattore riporta il
# risultato sulla scala del TSS prendendo come riferimento l'ora a sforzo 7 —
# «duro ma sostenibile», cioè grosso modo la soglia, che per definizione vale
# 100. Un numero dichiarato conta come **misura**, non come stima: è la sola
# cosa che un atleta senza fascia cardiaca può dare, ed è fatto apposta per
# essere confrontabile fra sedute.
SRPE_TO_TSS = 100 / (7 * 60)
RPE_MIN, RPE_MAX = 1, 10


@dataclass
class ActivityLoad:
    """Il costo di una seduta, con l'indicazione di come è stato ottenuto."""

    value: float
    source: str

    @property
    def is_measured(self) -> bool:
        """Falso solo quando il carico viene dalla sola durata.

        Lo sforzo percepito conta come misura: è dichiarato da chi l'ha
        sentito, non dedotto dal fatto che l'uscita sia durata un'ora.
        """
        return self.source != FROM_DURATION


@dataclass
class PmcPoint:
    """Un giorno sulla curva fitness/fatica/forma."""

    day: date
    load: float
    ctl: float  # fitness: il lavoro che il corpo ha assorbito
    atl: float  # fatica: il lavoro che deve ancora smaltire
    tsb: float  # forma: quanto ne è rimasto disponibile


# ============================================================================
# 1. Il costo di una seduta
# ============================================================================


def trimp(duration_sec: float | None, avg_hr: float | None,
          profile: AthleteProfile) -> float | None:
    """TRIMP di Banister: minuti × sforzo relativo, pesato esponenzialmente.

    L'esponenziale è il punto: un'ora al 90% della riserva cardiaca non costa
    il doppio di un'ora al 45%, ne costa quasi il quadruplo. È la ragione per
    cui sommare i chilometri non descrive l'allenamento.
    """
    if not duration_sec or not avg_hr:
        return None

    reserve_fraction = (avg_hr - profile.hr_rest) / profile.hr_reserve
    # Fuori dall'intervallo utile il modello non ha senso: una FC media sotto
    # il riposo è un errore di misura, sopra la massima è la massima.
    reserve_fraction = max(0.0, min(1.0, reserve_fraction))
    if reserve_fraction == 0:
        return 0.0

    minutes = duration_sec / 60
    return minutes * reserve_fraction * 0.64 * math.exp(profile.trimp_k * reserve_fraction)


def _power_load(duration_sec: float | None, avg_power: float | None,
                ftp: int | None) -> float | None:
    """TSS da potenza: la misura più pulita, quando il sensore c'è.

    Usa la potenza media al posto della normalizzata — che richiederebbe la
    serie secondo per secondo — quindi sottostima leggermente le uscite molto
    intermittenti.
    """
    if not duration_sec or not avg_power or not ftp:
        return None
    intensity = avg_power / ftp
    return (duration_sec / 3600) * intensity ** 2 * 100


def srpe(duration_sec: float | None, rpe: int | None) -> float | None:
    """sRPE di Foster: sforzo percepito per minuti, riportato sulla scala TSS.

    È la misura del carico più semplice che esista e regge il confronto con le
    altre: chi ha fatto la seduta sa quanto gli è costata. Vale soprattutto per
    chi non indossa una fascia — e per le sedute in cui la frequenza cardiaca
    mente, come la forza o le ripetute corte, dove il cuore arriva in ritardo
    sullo sforzo.
    """
    if not duration_sec or not rpe:
        return None
    effort = max(RPE_MIN, min(RPE_MAX, int(rpe)))
    return (duration_sec / 60) * effort * SRPE_TO_TSS


def activity_load(activity, profile: AthleteProfile) -> ActivityLoad | None:
    """Il carico di una seduta, col metro migliore che i suoi dati permettono.

    L'ordine non è negoziabile: la potenza misura il lavoro fatto, la frequenza
    cardiaca misura la risposta a quel lavoro, lo sforzo percepito misura quel
    che è costato a chi l'ha fatto, la durata non misura niente.

    Lo sforzo percepito viene dopo la frequenza cardiaca perché è una stima di
    una persona e non una lettura continua, ma prima della durata di parecchio:
    un'ora di ripetute e un'ora di camminata durano uguale e non costano
    uguale, e l'atleta questo lo sa anche senza sensori.
    """
    duration = getattr(activity, "duration_sec", None)

    from_power = _power_load(duration, getattr(activity, "avg_power", None), profile.ftp)
    if from_power is not None:
        return ActivityLoad(from_power, FROM_POWER)

    from_hr = trimp(duration, getattr(activity, "avg_hr", None), profile)
    if from_hr is not None:
        return ActivityLoad(from_hr * TRIMP_TO_TSS, FROM_HR)

    from_rpe = srpe(duration, getattr(activity, "rpe", None))
    if from_rpe is not None:
        return ActivityLoad(from_rpe, FROM_RPE)

    if duration:
        return ActivityLoad((duration / 3600) * LOAD_PER_HOUR_UNKNOWN, FROM_DURATION)

    return None


# ============================================================================
# 2. L'accumulo nel tempo
# ============================================================================


def daily_loads(activities: list, profile: AthleteProfile) -> dict[date, float]:
    """Somma i carichi per giorno. Più sedute nello stesso giorno si sommano."""
    out: dict[date, float] = {}
    for activity in activities:
        start = getattr(activity, "start_time", None)
        if start is None:
            continue
        load = activity_load(activity, profile)
        if load is None:
            continue
        day = start.date()
        out[day] = out.get(day, 0.0) + load.value
    return out


# Su quanti giorni si stima il carico che c'era *prima* dell'inizio della serie.
SEED_DAYS = 28


def _seed(loads: dict[date, float], first_day: date, last_day: date) -> float:
    """Valore iniziale di CTL e ATL.

    Partire da zero disegnerebbe una fitness che «cresce» solo perché la
    finestra di dati inizia lì: un artefatto, non un allenamento. Si parte
    invece da una stima di cosa stava succedendo appena prima.

    La stima guarda le **prime quattro settimane** della finestra e non tutta:
    prima era la media di centottanta giorni, il che vuol dire che un blocco di
    lavoro fatto ad agosto decideva da dove partiva la curva di marzo. Le
    giornate immediatamente successive all'inizio sono l'unica cosa che
    somiglia alle giornate immediatamente precedenti, che è ciò che il seed
    vorrebbe rappresentare.
    """
    if not loads:
        return 0.0

    horizon = min(last_day, first_day + timedelta(days=SEED_DAYS - 1))
    span = max((horizon - first_day).days + 1, 1)
    total = sum(v for d, v in loads.items() if first_day <= d <= horizon)
    return total / span


def pmc_series(loads: dict[date, float], start: date, end: date) -> list[PmcPoint]:
    """Fitness, fatica e forma giorno per giorno.

    I giorni senza allenamento contano come carico zero — è proprio nei giorni
    di riposo che la fatica scende più della fitness, ed è lì che nasce la
    forma.
    """
    if end < start:
        return []

    seed = _seed(loads, start, end)
    ctl = atl = seed
    points: list[PmcPoint] = []

    day = start
    while day <= end:
        load = loads.get(day, 0.0)
        # La forma di oggi è il saldo di ieri: il lavoro appena fatto non ha
        # ancora prodotto né adattamento né recupero.
        tsb = ctl - atl
        ctl += (load - ctl) / CTL_DAYS
        atl += (load - atl) / ATL_DAYS
        points.append(PmcPoint(day, load, ctl, atl, tsb))
        day += timedelta(days=1)

    return points


def _window_sum(loads: dict[date, float], end: date, days: int) -> float:
    return sum(loads.get(end - timedelta(days=i), 0.0) for i in range(days))


def _training_days(loads: dict[date, float], end: date, days: int) -> int:
    return sum(1 for i in range(days) if loads.get(end - timedelta(days=i), 0.0) > 0)


def acwr(loads: dict[date, float], end: date) -> float | None:
    """L'ultima settimana contro la media delle tre **precedenti**.

    Le finestre non si sovrappongono: la settimana acuta non entra nel proprio
    denominatore. Nella versione accoppiata — quella di prima — raddoppiare il
    carico alzava anche il riferimento, e una salita netta usciva più mite di
    quello che era.

    Restituisce `None` finché non c'è un'abitudine di allenamento rispetto a
    cui misurare l'ultima settimana. Senza quella soglia, chi esce una volta
    ogni tre settimane si vedrebbe segnalare un sovrallenamento ogni volta che
    si allena — il rapporto sarebbe alto perché il denominatore è quasi zero,
    non perché ci sia un rischio.
    """
    if _training_days(loads, end, CHRONIC_DAYS) < ACWR_MIN_TRAINING_DAYS:
        return None

    acute = _window_sum(loads, end, ACUTE_DAYS)
    before = _window_sum(loads, end - timedelta(days=CHRONIC_OFFSET), CHRONIC_SPAN)
    chronic = before / (CHRONIC_SPAN / ACUTE_DAYS)
    if chronic <= 0:
        return None
    return acute / chronic


def monotony(loads: dict[date, float], end: date, days: int = 7) -> float | None:
    """Monotonia di Foster: quanto il carico è uguale tutti i giorni.

    Due settimane con lo stesso totale possono avere effetti opposti: una con
    giorni duri e giorni vuoti allena, una tutta uguale logora soltanto.
    """
    if _training_days(loads, end, days) < MONOTONY_MIN_TRAINING_DAYS:
        return None

    daily = [loads.get(end - timedelta(days=i), 0.0) for i in range(days)]
    average = sum(daily) / len(daily)
    variability = pstdev(daily)
    if variability == 0:
        # Tutti i giorni uguali: monotonia massima, non monotonia ignota.
        return MONOTONY_MAX
    return min((average / variability), MONOTONY_MAX)


def strain(loads: dict[date, float], end: date, days: int = 7) -> float | None:
    """Carico settimanale moltiplicato per la monotonia."""
    mono = monotony(loads, end, days)
    if mono is None:
        return None
    return _window_sum(loads, end, days) * mono


# ============================================================================
# Lettura dei numeri
# ============================================================================


def acwr_label(ratio: float | None) -> tuple[str, str]:
    """(etichetta, tono) per il rapporto acuto/cronico."""
    if ratio is None:
        return "non calcolabile", "neutral"
    if ratio >= ACWR_DANGER:
        return "salita troppo rapida", "bad"
    if ratio >= ACWR_HIGH:
        return "in aumento deciso", "warn"
    if ratio < ACWR_LOW:
        return "in scarico", "warn"
    return "progressione sostenibile", "good"


def form_label(tsb: float | None) -> tuple[str, str]:
    """(etichetta, tono) per la forma (TSB).

    Le fasce sono quelle di uso comune fra allenatori: sopra +25 si è freschi
    ma si sta perdendo il lavoro fatto, sotto −30 si è in buca.
    """
    if tsb is None:
        return "non calcolabile", "neutral"
    if tsb > 25:
        return "fresco, ma stai perdendo stimolo", "warn"
    if tsb > 5:
        return "fresco e pronto", "good"
    if tsb >= -10:
        return "in equilibrio", "good"
    if tsb >= -30:
        return "sotto carico, è dove si costruisce", "warn"
    return "affaticato", "bad"
