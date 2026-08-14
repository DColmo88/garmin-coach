"""Il livello di interpretazione sopra i dati.

Ogni pagina non mostra solo grafici: mostra cosa dicono. Tutto deterministico,
in Python puro — nessuna chiamata AI, nessun costo, nessuna latenza.
"""
from app.insights.domains import (
    MetricNote,
    PageReading,
    read_activities,
    read_body,
    read_health,
    read_performance,
    read_sleep,
)

__all__ = [
    "MetricNote",
    "PageReading",
    "read_activities",
    "read_body",
    "read_health",
    "read_performance",
    "read_sleep",
]
