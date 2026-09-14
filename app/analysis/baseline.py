"""Il normale di una persona, e quanto un valore se ne discosta.

Questo modulo esisteva già, sparso: il briefing calcolava quartili per
raccontare al modello «sonno 41, tipico 72-85», e il motore di prontezza
prendeva lo stesso 41 e lo usava così com'era. Due letture dello stesso numero,
una personalizzata e una assoluta, nello stesso schermo.

La versione assoluta era la più sbagliata delle due. Il punteggio del sonno di
Garmin è già una media di popolazione: 72 per chi dorme abitualmente 85 è una
notte da segnalare, e per chi sta di solito sui 60 è la notte migliore del
mese. Un punteggio di prontezza che dà lo stesso contributo nei due casi non
sta misurando la prontezza di nessuno dei due.

Qui c'è il pezzo comune: il rango percentile di un valore dentro la propria
storia recente. Zero vuol dire «il peggio degli ultimi due mesi», cento «il
meglio», cinquanta «una giornata come tante».

**Quando non si usa.** Sotto `MIN_POINTS` di storia non c'è una distribuzione
su cui ragionare, e si ricade sulla lettura assoluta: meglio un riferimento di
popolazione che un percentile calcolato su quattro giornate.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import median

# Sotto questo numero di misurazioni il percentile descrive il campione, non la
# persona. Dieci giorni sono poche ma abbastanza da distinguere un'abitudine.
MIN_POINTS = 10

# Su quanti giorni si guarda indietro per sapere cos'è normale.
WINDOW_DAYS = 60


@dataclass(frozen=True)
class Distribution:
    """La storia recente di una misura, pronta da interrogare."""

    values: tuple[float, ...]
    higher_is_better: bool = True

    @property
    def usable(self) -> bool:
        return len(self.values) >= MIN_POINTS

    @property
    def sorted(self) -> list[float]:
        return sorted(self.values)

    def quantile(self, fraction: float) -> float | None:
        """Quantile per interpolazione lineare.

        `statistics.quantiles` su liste corte è brusco: divide in bucket e
        restituisce i bordi, quindi su dodici valori il 25° percentile salta.
        """
        ordered = self.sorted
        if not ordered:
            return None
        if len(ordered) == 1:
            return ordered[0]
        position = fraction * (len(ordered) - 1)
        low = int(position)
        high = min(low + 1, len(ordered) - 1)
        weight = position - low
        return ordered[low] * (1 - weight) + ordered[high] * weight

    @property
    def typical(self) -> tuple[float | None, float | None]:
        """L'intervallo in cui cade metà delle giornate (25°-75° percentile).

        Più onesto di media ± deviazione standard su dati che normali non sono:
        il sonno ha una coda lunga da un lato solo, e la deviazione standard la
        tratta come se fosse simmetrica.
        """
        return self.quantile(0.25), self.quantile(0.75)

    @property
    def median(self) -> float | None:
        return median(self.values) if self.values else None

    def percentile_of(self, value: float | None) -> float | None:
        """Dove cade questo valore nella propria storia, da 0 a 100.

        Il rango è quello «medio»: i valori uguali contano metà, così una
        misura ripetuta spesso non si prende artificiosamente il fondo o il
        vertice della scala.

        Con `higher_is_better=False` — frequenza a riposo, stress — la scala si
        rovescia, così **cento vuol dire sempre "bene"** per chi legge a valle.
        Una scala che cambia verso da un fattore all'altro è una scala che
        prima o poi qualcuno somma nel verso sbagliato.
        """
        if value is None or not self.usable:
            return None

        below = sum(1 for v in self.values if v < value)
        equal = sum(1 for v in self.values if v == value)
        rank = 100 * (below + equal / 2) / len(self.values)
        return rank if self.higher_is_better else 100 - rank


def of(values, higher_is_better: bool = True) -> Distribution:
    """Costruisce una distribuzione scartando i buchi."""
    clean = tuple(
        float(v) for v in values if v is not None and not isinstance(v, bool)
    )
    return Distribution(clean, higher_is_better)
