"""Il livello di interpretazione sopra i dati.

Ogni pagina non mostra solo grafici: mostra cosa dicono. Tutto deterministico,
in Python puro — nessuna chiamata AI, nessun costo, nessuna latenza.
"""
from app.insights.domains import (
    MetricNote,
    PageReading,
    hrv_status_label,
    read_activities,
    read_body,
    read_fitness,
    read_health,
    read_sleep,
    training_status_label,
)

__all__ = [
    "MetricNote",
    "PageReading",
    "hrv_status_label",
    "read_activities",
    "read_body",
    "read_fitness",
    "read_health",
    "read_sleep",
    "training_status_label",
]
