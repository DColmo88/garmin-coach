"""Analisi deterministica del carico di allenamento.

Garmin, per questo account, non restituisce nessun `training_load`: 28 giorni
di metriche, zero valori. Metà del linguaggio dell'app — «carico acuto»,
«rischio di sovrallenamento», il 15% del punteggio di prontezza — poggiava su
un campo vuoto e restituiva sempre lo stesso valore neutro.

Questo package ricalcola quel carico dai dati che Garmin *dà davvero*: durata,
frequenza cardiaca media e massima, potenza. Sono le stesse formule che usa la
letteratura (Banister per il TRIMP, il Performance Management Chart di
Coggan/Allen per fitness-fatica-forma), quindi non c'è niente di inventato:
solo aritmetica su numeri che erano già nel database.

Tutto è Python puro. Nessuna chiamata AI, nessuna chiamata di rete.
"""
from app.analysis.load import (
    ActivityLoad,
    PmcPoint,
    acwr,
    activity_load,
    daily_loads,
    monotony,
    pmc_series,
    strain,
    trimp,
)
from app.analysis.profile import AthleteProfile, resolve_profile
from app.analysis.records import PersonalRecord, personal_records
from app.analysis.zones import (
    ZONES,
    ZoneDistribution,
    observed_bounds,
    zone_boundaries,
    zone_distribution,
)

__all__ = [
    "ActivityLoad",
    "AthleteProfile",
    "PersonalRecord",
    "PmcPoint",
    "ZONES",
    "ZoneDistribution",
    "activity_load",
    "acwr",
    "daily_loads",
    "monotony",
    "personal_records",
    "pmc_series",
    "resolve_profile",
    "strain",
    "trimp",
    "observed_bounds",
    "zone_boundaries",
    "zone_distribution",
]
