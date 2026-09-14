"""Record personali, ricavati dallo storico invece che chiesti a Garmin.

Garmin i suoi record li tiene, ma solo sul proprio metro e solo per l'account:
non sa niente di come è andata *questa* stagione, e la sua lista non entra in
nessuna delle analisi dell'app. Qui i primati si ricalcolano dalle attività già
scaricate, quindi restano coerenti con tutto il resto e si possono filtrare per
periodo.

I tempi sulle distanze classiche sono normalizzati sul ritmo medio: un'uscita
di 10,4 km vale come un 10 km, riportando il tempo alla distanza esatta. È il
meglio che si possa fare senza i parziali secondo per secondo, e viene detto
esplicitamente nell'interfaccia.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# Le distanze su cui ha senso avere un primato, in metri.
STANDARD_DISTANCES = [
    (1_000, "1 km"),
    (5_000, "5 km"),
    (10_000, "10 km"),
    (21_097, "Mezza maratona"),
    (42_195, "Maratona"),
]

# Finestra di accettazione attorno alla distanza nominale. Sotto il 97% non si
# può parlare di quel primato; sopra il 110% l'uscita era un'altra cosa.
DISTANCE_MIN_RATIO = 0.97
DISTANCE_MAX_RATIO = 1.10

# Sotto i 3 km il ritmo medio dice poco: riscaldamento e defaticamento pesano
# più della parte corsa.
MIN_DISTANCE_FOR_PACE = 3_000


@dataclass
class PersonalRecord:
    """Un primato: cosa, quanto, quando, e in quale attività."""

    key: str
    label: str
    value: str
    detail: str = ""
    day: date | None = None
    activity_id: int | None = None
    estimated: bool = False
    # Vero quando il dettaglio contiene già la data (il record settimanale
    # porta l'intervallo): a chi lo mostra serve per non ripeterla.
    detail_has_date: bool = False


def _is_run(activity) -> bool:
    kind = (getattr(activity, "activity_type", None) or "").lower()
    return "run" in kind


def _fmt_time(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _fmt_pace(sec_per_km: float) -> str:
    minutes, secs = divmod(int(round(sec_per_km)), 60)
    return f"{minutes}:{secs:02d} /km"


def _day_of(activity) -> date | None:
    start = getattr(activity, "start_time", None)
    return start.date() if start else None


def _best_at_distance(activities: list, target_m: int, label: str) -> PersonalRecord | None:
    """Il tempo migliore su una distanza, normalizzato sul ritmo medio."""
    best_time: float | None = None
    best_activity = None
    was_exact = False

    for activity in activities:
        distance = getattr(activity, "distance_m", None)
        duration = getattr(activity, "duration_sec", None)
        if not distance or not duration:
            continue
        ratio = distance / target_m
        if not DISTANCE_MIN_RATIO <= ratio <= DISTANCE_MAX_RATIO:
            continue

        normalized = duration / ratio  # tempo riportato alla distanza esatta
        if best_time is None or normalized < best_time:
            best_time = normalized
            best_activity = activity
            was_exact = abs(ratio - 1) < 0.005

    if best_time is None or best_activity is None:
        return None

    return PersonalRecord(
        key=f"dist_{target_m}",
        label=label,
        value=_fmt_time(best_time),
        detail=_fmt_pace(best_time / (target_m / 1000)),
        day=_day_of(best_activity),
        activity_id=getattr(best_activity, "external_id", None),
        estimated=not was_exact,
    )


def _extreme(activities: list, attr: str, key: str, label: str,
             render, detail=None) -> PersonalRecord | None:
    """Il massimo di un attributo fra le attività."""
    best = None
    best_value = None
    for activity in activities:
        value = getattr(activity, attr, None)
        if value is None:
            continue
        if best_value is None or value > best_value:
            best_value, best = value, activity
    if best is None:
        return None
    return PersonalRecord(
        key=key, label=label, value=render(best_value),
        detail=detail(best) if detail else "",
        day=_day_of(best),
        activity_id=getattr(best, "external_id", None),
    )


def _fastest_pace(activities: list) -> PersonalRecord | None:
    """Il ritmo medio più veloce su un'uscita di almeno 3 km."""
    best_pace = None
    best = None
    for activity in activities:
        distance = getattr(activity, "distance_m", None)
        duration = getattr(activity, "duration_sec", None)
        if not distance or not duration or distance < MIN_DISTANCE_FOR_PACE:
            continue
        pace = duration / (distance / 1000)
        if best_pace is None or pace < best_pace:
            best_pace, best = pace, activity
    if best is None or best_pace is None:
        return None
    return PersonalRecord(
        key="pace", label="Ritmo medio più veloce", value=_fmt_pace(best_pace),
        detail=f"su {(best.distance_m or 0) / 1000:.1f} km".replace(".", ","),
        day=_day_of(best),
        activity_id=getattr(best, "external_id", None),
    )


def _best_week(activities: list) -> PersonalRecord | None:
    """La settimana con più chilometri, su finestra mobile di 7 giorni."""
    by_day: dict[date, float] = {}
    for activity in activities:
        day = _day_of(activity)
        distance = getattr(activity, "distance_m", None)
        if day is None or not distance:
            continue
        by_day[day] = by_day.get(day, 0.0) + distance

    if not by_day:
        return None

    best_total = 0.0
    best_end: date | None = None
    for end in by_day:
        total = sum(
            by_day.get(end - timedelta(days=i), 0.0) for i in range(7)
        )
        if total > best_total:
            best_total, best_end = total, end

    if best_end is None or best_total <= 0:
        return None

    start = best_end - timedelta(days=6)
    return PersonalRecord(
        key="week", label="Settimana più lunga",
        value=f"{best_total / 1000:.1f} km".replace(".", ","),
        detail=f"{start.strftime('%d/%m')} – {best_end.strftime('%d/%m/%Y')}",
        day=best_end,
        detail_has_date=True,
    )


def _fastest_ride(activities: list) -> PersonalRecord | None:
    """Il giro più veloce, in km/h, su almeno 15 km.

    Sotto quella soglia un giro è troppo corto perché la media dica qualcosa:
    bastano una discesa lunga o un semaforo in meno.
    """
    best_kmh = None
    best = None
    for activity in activities:
        distance = getattr(activity, "distance_m", None)
        duration = getattr(activity, "duration_sec", None)
        if not distance or not duration or distance < 15_000:
            continue
        kmh = (distance / 1000) / (duration / 3600)
        if best_kmh is None or kmh > best_kmh:
            best_kmh, best = kmh, activity
    if best is None:
        return None
    return PersonalRecord(
        key="speed", label="Giro più veloce",
        value=f"{best_kmh:.1f} km/h".replace(".", ","),
        detail=f"su {(best.distance_m or 0) / 1000:.0f} km",
        day=_day_of(best),
        activity_id=getattr(best, "external_id", None),
    )


def _best_power(activities: list) -> PersonalRecord | None:
    """La potenza media più alta su un'uscita di almeno un'ora."""
    best = None
    best_watt = None
    for activity in activities:
        watt = getattr(activity, "avg_power", None)
        duration = getattr(activity, "duration_sec", None)
        if not watt or not duration or duration < 3600:
            continue
        if best_watt is None or watt > best_watt:
            best_watt, best = watt, activity
    if best is None:
        return None
    return PersonalRecord(
        key="power", label="Potenza media più alta",
        value=f"{round(best_watt)} W",
        detail=f"su {(best.duration_sec or 0) / 3600:.1f} h".replace(".", ","),
        day=_day_of(best),
        activity_id=getattr(best, "external_id", None),
    )


def personal_records(activities: list, primary_sport: str = "running") -> list[PersonalRecord]:
    """I primati che hanno senso per come si allena questa persona.

    Volume, dislivello e durata valgono per qualunque sport e si guardano
    sempre su tutto. Cambia la parte prestativa: per chi corre sono i tempi
    sulle distanze classiche, per chi va in bici sono velocità e potenza —
    a un ciclista un primato sui 5 km di corsa non dice niente.
    """
    from app.sports import CYCLING, sport_of

    runs = [a for a in activities if _is_run(a)]
    rides = [a for a in activities if sport_of(a).family == CYCLING]

    common: list[PersonalRecord | None] = [
        _extreme(activities, "distance_m", "longest", "Uscita più lunga",
                 lambda v: f"{v / 1000:.1f} km".replace(".", ",")),
        _extreme(activities, "duration_sec", "duration", "Seduta più lunga",
                 _fmt_time),
        _extreme(activities, "elevation_gain_m", "elevation", "Più dislivello",
                 lambda v: f"{round(v)} m"),
    ]

    if primary_sport == CYCLING:
        specific: list[PersonalRecord | None] = [
            _fastest_ride(rides),
            _best_power(rides),
            _best_week(rides),
            # I tempi di corsa restano, in coda: chi pedala spesso corre anche.
            _best_at_distance(runs, 10_000, "10 km di corsa"),
        ]
    else:
        specific = [
            *(_best_at_distance(runs, meters, label) for meters, label in STANDARD_DISTANCES),
            _fastest_pace(runs),
            _best_week(runs),
            _fastest_ride(rides),
        ]

    return [r for r in specific + common if r is not None]
