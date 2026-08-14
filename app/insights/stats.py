"""Statistica di base, tollerante ai buchi nei dati.

I dati Garmin hanno buchi: notti non registrate, giorni senza orologio. Ogni
funzione qui ignora i `None` e restituisce `None` quando non ha abbastanza
materiale per dire qualcosa di sensato — meglio tacere che inventare un trend
su due punti.
"""
from __future__ import annotations

from statistics import median, pstdev
from typing import Sequence

# Sotto questa soglia un "trend" è rumore, non un andamento.
MIN_POINTS_FOR_TREND = 5


def clean(values: Sequence[float | None]) -> list[float]:
    """Solo i valori presenti, come float."""
    return [float(v) for v in values if v is not None]


def mean(values: Sequence[float | None]) -> float | None:
    data = clean(values)
    return sum(data) / len(data) if data else None


def med(values: Sequence[float | None]) -> float | None:
    data = clean(values)
    return median(data) if data else None


def spread(values: Sequence[float | None]) -> float | None:
    """Deviazione standard: quanto il dato è regolare."""
    data = clean(values)
    return pstdev(data) if len(data) >= 2 else None


def delta(recent: Sequence[float | None], earlier: Sequence[float | None]) -> float | None:
    """Differenza fra le medie di due periodi. Positiva se il recente è più alto."""
    a, b = mean(recent), mean(earlier)
    return None if a is None or b is None else a - b


def pct_change(recent: Sequence[float | None], earlier: Sequence[float | None]) -> float | None:
    a, b = mean(recent), mean(earlier)
    if a is None or b is None or b == 0:
        return None
    return 100 * (a - b) / b


def slope(values: Sequence[float | None]) -> float | None:
    """Pendenza della retta di regressione: variazione media per punto.

    Serve a distinguere "sta calando" da "oscilla". Richiede almeno
    MIN_POINTS_FOR_TREND valori.
    """
    points = [(i, float(v)) for i, v in enumerate(values) if v is not None]
    if len(points) < MIN_POINTS_FOR_TREND:
        return None

    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        return None
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    return numerator / denominator


def split(values: Sequence[float | None], recent_days: int) -> tuple[list, list]:
    """Divide una serie crescente in (periodo recente, periodo precedente)."""
    if recent_days <= 0 or len(values) <= recent_days:
        return list(values), []
    return list(values[-recent_days:]), list(values[:-recent_days])


def coverage(values: Sequence[float | None]) -> float:
    """Frazione di giorni con dato: sotto il 50% ogni media è poco affidabile."""
    return len(clean(values)) / len(values) if values else 0.0


def latest(values: Sequence[float | None]) -> float | None:
    """Ultimo valore presente in una serie crescente."""
    for value in reversed(values):
        if value is not None:
            return float(value)
    return None


def consecutive_at_least(values: Sequence[float | None], threshold: float) -> int:
    """Quanti giorni consecutivi, a partire dal più recente, stanno sopra soglia."""
    count = 0
    for value in reversed(values):
        if value is None or value < threshold:
            break
        count += 1
    return count


def fmt(value: float | None, digits: int = 0, suffix: str = "") -> str:
    """Formattazione per i testi degli insight: niente decimali inutili."""
    if value is None:
        return "—"
    if digits == 0:
        return f"{round(value):,}".replace(",", ".") + suffix
    return f"{value:.{digits}f}".replace(".", ",") + suffix


def fmt_duration(seconds: float | None) -> str:
    """Secondi in '7h 20m'."""
    if not seconds:
        return "—"
    hours, minutes = divmod(int(seconds) // 60, 60)
    return f"{hours}h {minutes:02d}m"
