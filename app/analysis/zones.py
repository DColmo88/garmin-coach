"""Distribuzione delle intensità: dove sta davvero il tempo di allenamento.

Finora l'app classificava un'intera uscita come «facile» o «dura» in base alla
FC media. È una semplificazione che si rompe proprio sulle sedute che contano:
un fartlek con dieci ripetute e dieci recuperi ha una media tiepida e non è né
facile né dura — è entrambe le cose.

Garmin il tempo per zona lo calcola già, secondo per secondo. La sync lo salva
in `Activity.hr_zones_json`; qui lo si somma e lo si legge.
"""
from __future__ import annotations

from dataclasses import dataclass

# Le cinque zone di Garmin, in frazione di FC massima. Sono le stesse soglie
# di default dell'orologio, così i numeri dell'app e quelli del polso coincidono.
ZONES = [
    {"n": 1, "name": "Recupero", "lo": 0.50, "hi": 0.60,
     "what": "Il corpo smaltisce. Non allena, ripara."},
    {"n": 2, "name": "Aerobica", "lo": 0.60, "hi": 0.70,
     "what": "Dove si costruisce il motore. Dovrebbe essere la maggior parte del tempo."},
    {"n": 3, "name": "Medio", "lo": 0.70, "hi": 0.80,
     "what": "La zona grigia: stanca come una seduta dura senza darne lo stimolo."},
    {"n": 4, "name": "Soglia", "lo": 0.80, "hi": 0.90,
     "what": "Alza la velocità sostenibile. Costosa: una o due volte a settimana."},
    {"n": 5, "name": "Massimale", "lo": 0.90, "hi": 1.00,
     "what": "Potenza aerobica pura. A piccole dosi, e mai da stanchi."},
]

# La regola 80/20: nei programmi che funzionano, quattro quinti del tempo
# stanno in zona 1-2. Non è una moda, è quello che si osserva negli atleti di
# resistenza a tutti i livelli.
EASY_TARGET_PCT = 80
# Sotto questa soglia di minuti totali non si commenta una distribuzione:
# tre uscite non sono un modo di allenarsi.
MIN_MINUTES_FOR_READING = 120


@dataclass
class ZoneDistribution:
    """Quanto tempo in ciascuna zona, in un periodo."""

    seconds: list[float]  # cinque valori, uno per zona
    activities_counted: int
    activities_total: int

    @property
    def total_seconds(self) -> float:
        return sum(self.seconds)

    @property
    def has_data(self) -> bool:
        return self.total_seconds > 0

    @property
    def percentages(self) -> list[float]:
        total = self.total_seconds
        if not total:
            return [0.0] * len(self.seconds)
        return [100 * s / total for s in self.seconds]

    @property
    def easy_pct(self) -> float:
        """Zone 1 e 2: il lavoro che costruisce senza costare."""
        pct = self.percentages
        return pct[0] + pct[1]

    @property
    def grey_pct(self) -> float:
        """Zona 3: la fascia che affatica senza allenare abbastanza."""
        return self.percentages[2]

    @property
    def hard_pct(self) -> float:
        """Zone 4 e 5: il lavoro di qualità."""
        pct = self.percentages
        return pct[3] + pct[4]

    @property
    def coverage(self) -> float:
        """Frazione di attività per cui il dato per zona esiste."""
        if not self.activities_total:
            return 0.0
        return self.activities_counted / self.activities_total

    @property
    def is_readable(self) -> bool:
        return self.total_seconds >= MIN_MINUTES_FOR_READING * 60


# Da dove vengono i confini mostrati a schermo.
BOUNDS_FROM_DEVICE = "orologio"
BOUNDS_FROM_HR_MAX = "percentuale della FC massima"


def zone_boundaries(hr_max: int, observed: list | None = None
                    ) -> tuple[list[tuple[int, int]], str]:
    """I bpm di inizio e fine di ogni zona, e da dove arrivano.

    Se l'orologio ha dichiarato i propri confini — arrivano insieme ai secondi
    per zona, nella stessa risposta — si mostrano **quelli**: sono la scala con
    cui i secondi del grafico sono stati contati davvero.

    La ricaduta sulle percentuali della FC massima resta per chi non ha ancora
    attività con il dato, e per chi usa una sorgente che non lo fornisce. Ma è
    una ricaduta e va dichiarata: l'orologio può avere le zone tarate su
    riserva cardiaca o su soglia anaerobica, e in quel caso i confini calcolati
    qui non sono quelli con cui i minuti sono stati sommati. Il grafico diceva
    una cosa e la legenda un'altra, senza che niente lo segnalasse.
    """
    if observed and len([b for b in observed if b]) >= len(ZONES):
        lows = [int(round(float(b))) for b in observed[: len(ZONES)]]
        pairs = [
            (lows[i], lows[i + 1] if i + 1 < len(lows) else int(round(hr_max)))
            for i in range(len(lows))
        ]
        return pairs, BOUNDS_FROM_DEVICE

    derived = [(round(z["lo"] * hr_max), round(z["hi"] * hr_max)) for z in ZONES]
    return derived, BOUNDS_FROM_HR_MAX


def observed_bounds(activities: list) -> list | None:
    """I confini dichiarati dall'attività più recente che ne ha.

    Non una media fra attività: se l'atleta ha cambiato le zone sull'orologio,
    quelle giuste sono le ultime, e mediarle darebbe confini che non sono mai
    stati veri per nessuna seduta.
    """
    for activity in activities:
        bounds = getattr(activity, "hr_zone_bounds_json", None)
        if bounds and any(b for b in bounds):
            return bounds
    return None


def zone_distribution(activities: list) -> ZoneDistribution:
    """Somma i secondi per zona sulle attività che hanno il dato.

    Le attività senza dato non vengono stimate: comparirebbero come zeri e
    sposterebbero le percentuali. Meglio dichiarare la copertura parziale.
    """
    totals = [0.0] * len(ZONES)
    counted = 0

    for activity in activities:
        zones = getattr(activity, "hr_zones_json", None)
        if not zones:
            continue
        counted += 1
        for i, seconds in enumerate(zones[: len(totals)]):
            totals[i] += float(seconds or 0)

    return ZoneDistribution(totals, counted, len(activities))


def read_distribution(dist: ZoneDistribution) -> tuple[str, str]:
    """(commento, tono) sulla distribuzione delle intensità."""
    if not dist.is_readable:
        return (
            "Servono più allenamenti col dato per zona prima di poter dire "
            "come distribuisci le intensità.",
            "neutral",
        )

    easy = round(dist.easy_pct)
    grey = round(dist.grey_pct)
    hard = round(dist.hard_pct)

    if grey >= 35:
        return (
            f"Il {grey}% del tempo sta in zona 3. È la fascia peggiore in cui "
            "stare: costa quanto una seduta di qualità e non ne dà i benefici. "
            "Rallenta le uscite facili e alza quelle dure.",
            "warn",
        )
    if easy < 60:
        return (
            f"Solo il {easy}% del tempo è in zona 1-2, contro un riferimento "
            f"di circa {EASY_TARGET_PCT}%. Corri quasi sempre a un'intensità "
            "che affatica: le sedute dure ne risentono.",
            "warn",
        )
    if easy >= EASY_TARGET_PCT and hard >= 10:
        return (
            f"{easy}% facile, {hard}% duro: la distribuzione è quella giusta. "
            "Il facile è davvero facile e il duro è davvero duro.",
            "good",
        )
    if hard < 5:
        return (
            f"{easy}% del tempo in zona 1-2, ma solo il {hard}% in zona 4-5. "
            "La base c'è, manca lo stimolo: una seduta di qualità a settimana "
            "cambierebbe il quadro.",
            "warn",
        )
    return (
        f"{easy}% facile, {grey}% medio, {hard}% duro. Distribuzione "
        "ragionevole, con un po' di margine per spostare il medio verso i "
        "due estremi.",
        "neutral",
    )
