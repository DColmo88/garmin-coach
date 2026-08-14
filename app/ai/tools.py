"""I tool che il coach AI può chiamare per consultare i dati.

Questo modulo è la risposta al problema "quanti dati mando al modello".
Il system prompt contiene solo lo stato di oggi; se la conversazione tocca il
mese scorso o una singola uscita, il modello chiama uno di questi tool e
riceve **solo quel dato, già aggregato**.

Due regole che tengono bassi i costi:

1. Le serie lunghe non escono mai grezze. Oltre ~35 giorni si aggrega per
   settimana, oltre ~180 per mese: un anno di dati diventa 12 righe.
2. Ogni tool ha un tetto massimo di righe restituite.

L'output è testo compatto (non JSON): costa meno token e i modelli lo leggono
altrettanto bene.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import queries as q
from app.ai.base import ToolSpec
from app.db.models import Activity, BodyComposition, DailyWellness, SleepRecord, TrainingMetric
from app.goals import active_goal, describe_goal

logger = logging.getLogger(__name__)

MAX_ROWS = 40
WEEKLY_THRESHOLD_DAYS = 35
MONTHLY_THRESHOLD_DAYS = 180

_DATE_ARG = {"type": "string", "description": "Data ISO (AAAA-MM-GG)"}


# ============================================================================
# Helper
# ============================================================================


def _parse_day(value: Any, default: date) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(str(value)[:10])
    except (ValueError, TypeError):
        return default


def _range(arguments: dict, default_days: int = 7) -> tuple[date, date]:
    """Intervallo richiesto, con default sugli ultimi giorni."""
    end = _parse_day(arguments.get("date_to"), date.today())
    start = _parse_day(arguments.get("date_from"), end - timedelta(days=default_days - 1))
    if start > end:
        start, end = end, start
    return start, end


def _avg(values: list) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _n(value: Any, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{suffix}" if isinstance(value, float) else f"{value}{suffix}"


def _bucket_key(day: date, span_days: int) -> tuple[str, str]:
    """Etichetta del periodo in cui ricade un giorno, in base all'ampiezza."""
    if span_days <= WEEKLY_THRESHOLD_DAYS:
        return day.isoformat(), day.strftime("%d/%m")
    if span_days <= MONTHLY_THRESHOLD_DAYS:
        monday = day - timedelta(days=day.weekday())
        return monday.isoformat(), f"sett. del {monday.strftime('%d/%m')}"
    return day.strftime("%Y-%m"), day.strftime("%m/%Y")


def _aggregate(rows: list, fields: list[str], span_days: int) -> list[dict]:
    """Raggruppa le righe per giorno/settimana/mese e ne fa la media."""
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        key, label = _bucket_key(row.day, span_days)
        bucket = buckets.setdefault(key, {"label": label, "_values": {f: [] for f in fields}})
        for field in fields:
            bucket["_values"][field].append(getattr(row, field, None))

    out = []
    for key in sorted(buckets)[-MAX_ROWS:]:
        bucket = buckets[key]
        entry = {"label": bucket["label"]}
        for field in fields:
            entry[field] = _avg(bucket["_values"][field])
        out.append(entry)
    return out


def _no_data(what: str, start: date, end: date) -> str:
    return (
        f"Nessun dato di {what} tra il {start.strftime('%d/%m/%Y')} e "
        f"il {end.strftime('%d/%m/%Y')}."
    )


def _period_note(span_days: int) -> str:
    if span_days <= WEEKLY_THRESHOLD_DAYS:
        return ""
    if span_days <= MONTHLY_THRESHOLD_DAYS:
        return " (medie settimanali)"
    return " (medie mensili)"


# ============================================================================
# Implementazione dei tool
# ============================================================================


def tool_get_wellness(db: Session, user_id: int, arguments: dict) -> str:
    """Passi, FC a riposo, stress, Body Battery in un periodo."""
    start, end = _range(arguments, 7)
    span = (end - start).days + 1
    rows = list(db.scalars(
        select(DailyWellness)
        .where(DailyWellness.user_id == user_id,
               DailyWellness.day >= start, DailyWellness.day <= end)
        .order_by(DailyWellness.day)
    ).all())
    if not rows:
        return _no_data("benessere quotidiano", start, end)

    fields = ["total_steps", "resting_hr", "avg_stress", "body_battery_high", "body_battery_low"]
    lines = [
        f"{e['label']}: passi {_n(e['total_steps'])}, FC riposo {_n(e['resting_hr'], 0)}, "
        f"stress {_n(e['avg_stress'], 0)}, Body Battery {_n(e['body_battery_low'], 0)}–"
        f"{_n(e['body_battery_high'], 0)}"
        for e in _aggregate(rows, fields, span)
    ]
    return f"Benessere{_period_note(span)}:\n" + "\n".join(lines)


def tool_get_sleep(db: Session, user_id: int, arguments: dict) -> str:
    """Durata, score e fasi del sonno in un periodo."""
    start, end = _range(arguments, 7)
    span = (end - start).days + 1
    rows = list(db.scalars(
        select(SleepRecord)
        .where(SleepRecord.user_id == user_id,
               SleepRecord.day >= start, SleepRecord.day <= end)
        .order_by(SleepRecord.day)
    ).all())
    if not rows:
        return _no_data("sonno", start, end)

    fields = ["total_sleep_sec", "deep_sleep_sec", "rem_sleep_sec", "sleep_score", "resting_hr"]
    lines = []
    for e in _aggregate(rows, fields, span):
        hours = (e["total_sleep_sec"] or 0) / 3600
        deep_pct = (
            100 * e["deep_sleep_sec"] / e["total_sleep_sec"]
            if e["deep_sleep_sec"] and e["total_sleep_sec"] else None
        )
        lines.append(
            f"{e['label']}: {hours:.1f}h, score {_n(e['sleep_score'], 0)}, "
            f"profondo {_n(deep_pct, 0, '%')}, FC notturna {_n(e['resting_hr'], 0)}"
        )
    return f"Sonno{_period_note(span)}:\n" + "\n".join(lines)


def tool_get_activities(db: Session, user_id: int, arguments: dict) -> str:
    """Elenco degli allenamenti, con filtro per tipo."""
    start, end = _range(arguments, 30)
    query = (
        select(Activity)
        .where(Activity.user_id == user_id,
               Activity.start_time >= datetime.combine(start, datetime.min.time()),
               Activity.start_time <= datetime.combine(end, datetime.max.time()))
        .order_by(Activity.start_time.desc())
        .limit(MAX_ROWS)
    )
    wanted = (arguments.get("activity_type") or "").strip().lower()
    if wanted:
        query = query.where(Activity.activity_type.ilike(f"%{wanted}%"))

    rows = list(db.scalars(query).all())
    if not rows:
        extra = f" di tipo «{wanted}»" if wanted else ""
        return _no_data(f"allenamenti{extra}", start, end)

    total_km = sum((a.distance_m or 0) for a in rows) / 1000
    total_min = sum((a.duration_sec or 0) for a in rows) / 60

    lines = []
    for a in rows:
        when = a.start_time.strftime("%d/%m/%Y") if a.start_time else "—"
        km = (a.distance_m or 0) / 1000
        minutes = (a.duration_sec or 0) / 60
        pace = ""
        if a.distance_m and a.duration_sec and a.distance_m > 500:
            sec_per_km = a.duration_sec / (a.distance_m / 1000)
            pace = f", passo {int(sec_per_km // 60)}:{int(sec_per_km % 60):02d}/km"
        lines.append(
            f"[id {a.garmin_activity_id}] {when} · {a.activity_type or 'attività'} · "
            f"{km:.1f} km · {minutes:.0f} min{pace} · FC media {_n(a.avg_hr, 0)}"
        )

    header = (
        f"{len(rows)} allenamenti dal {start.strftime('%d/%m/%Y')} al "
        f"{end.strftime('%d/%m/%Y')}: {total_km:.1f} km, {total_min:.0f} minuti totali."
    )
    return header + "\n" + "\n".join(lines)


def tool_get_activity_detail(db: Session, user_id: int, arguments: dict) -> str:
    """Tutti i dettagli di un singolo allenamento."""
    try:
        activity_id = int(arguments.get("activity_id"))
    except (TypeError, ValueError):
        return "Serve l'id numerico dell'allenamento (lo trovi con get_activities)."

    a = q.get_activity(db, user_id, activity_id)
    if a is None:
        return f"Nessun allenamento con id {activity_id} tra i tuoi dati."

    parts = [
        f"{a.name or 'Allenamento'} ({a.activity_type or 'tipo n/d'})",
        f"Data: {a.start_time.strftime('%d/%m/%Y %H:%M') if a.start_time else '—'}",
        f"Distanza: {_n((a.distance_m or 0) / 1000, 2, ' km')}",
        f"Durata: {_n((a.duration_sec or 0) / 60, 0, ' min')}",
        f"FC media/max: {_n(a.avg_hr, 0)}/{_n(a.max_hr, 0)}",
        f"Dislivello: {_n(a.elevation_gain_m, 0, ' m')}",
        f"Calorie: {_n(a.calories, 0)}",
        f"Cadenza media: {_n(a.avg_cadence, 0)}",
        f"Training effect aerobico/anaerobico: {_n(a.aerobic_te, 1)}/{_n(a.anaerobic_te, 1)}",
    ]
    return "\n".join(parts)


def tool_get_performance(db: Session, user_id: int, arguments: dict) -> str:
    """VO2max, carico, HRV e training status nel tempo."""
    start, end = _range(arguments, 30)
    span = (end - start).days + 1
    rows = list(db.scalars(
        select(TrainingMetric)
        .where(TrainingMetric.user_id == user_id,
               TrainingMetric.day >= start, TrainingMetric.day <= end)
        .order_by(TrainingMetric.day)
    ).all())
    if not rows:
        return _no_data("performance", start, end)

    fields = ["vo2max", "training_load", "hrv_weekly_avg", "readiness_score"]
    lines = [
        f"{e['label']}: VO2max {_n(e['vo2max'], 1)}, carico {_n(e['training_load'], 0)}, "
        f"HRV {_n(e['hrv_weekly_avg'], 0)}, readiness Garmin {_n(e['readiness_score'], 0)}"
        for e in _aggregate(rows, fields, span)
    ]
    status = next((r.training_status for r in reversed(rows) if r.training_status), None)
    hrv = next((r.hrv_status for r in reversed(rows) if r.hrv_status), None)
    footer = f"\nTraining status attuale: {status or 'n/d'}. Stato HRV: {hrv or 'n/d'}."
    return f"Performance{_period_note(span)}:\n" + "\n".join(lines) + footer


def tool_get_body(db: Session, user_id: int, arguments: dict) -> str:
    """Peso e composizione corporea."""
    start, end = _range(arguments, 90)
    span = (end - start).days + 1
    rows = list(db.scalars(
        select(BodyComposition)
        .where(BodyComposition.user_id == user_id,
               BodyComposition.day >= start, BodyComposition.day <= end)
        .order_by(BodyComposition.day)
    ).all())
    if not rows:
        return (
            _no_data("composizione corporea", start, end)
            + " Servono misurazioni da una bilancia collegata a Garmin."
        )

    fields = ["weight_g", "bmi", "body_fat_pct", "muscle_mass_g"]
    lines = [
        f"{e['label']}: peso {_n((e['weight_g'] or 0) / 1000 or None, 1, ' kg')}, "
        f"BMI {_n(e['bmi'], 1)}, grasso {_n(e['body_fat_pct'], 1, '%')}, "
        f"massa muscolare {_n((e['muscle_mass_g'] or 0) / 1000 or None, 1, ' kg')}"
        for e in _aggregate(rows, fields, span)
    ]
    return f"Composizione corporea{_period_note(span)}:\n" + "\n".join(lines)


def tool_get_goal(db: Session, user_id: int, arguments: dict) -> str:
    """Obiettivo attivo dell'utente."""
    return describe_goal(active_goal(db, user_id))


def tool_compare_periods(db: Session, user_id: int, arguments: dict) -> str:
    """Confronto diretto fra due periodi — la domanda più frequente in chat."""
    days = max(1, min(int(arguments.get("days") or 30), 365))
    end_recent = date.today()
    start_recent = end_recent - timedelta(days=days - 1)
    end_before = start_recent - timedelta(days=1)
    start_before = end_before - timedelta(days=days - 1)

    def stats(start: date, end: date) -> dict[str, Any]:
        wellness = list(db.scalars(
            select(DailyWellness).where(
                DailyWellness.user_id == user_id,
                DailyWellness.day >= start, DailyWellness.day <= end)
        ).all())
        sleep = list(db.scalars(
            select(SleepRecord).where(
                SleepRecord.user_id == user_id,
                SleepRecord.day >= start, SleepRecord.day <= end)
        ).all())
        acts = list(db.scalars(
            select(Activity).where(
                Activity.user_id == user_id,
                Activity.start_time >= datetime.combine(start, datetime.min.time()),
                Activity.start_time <= datetime.combine(end, datetime.max.time()))
        ).all())
        return {
            "FC a riposo": _avg([w.resting_hr for w in wellness]),
            "passi al giorno": _avg([w.total_steps for w in wellness]),
            "stress": _avg([w.avg_stress for w in wellness]),
            "score del sonno": _avg([s.sleep_score for s in sleep]),
            "ore di sonno": (_avg([s.total_sleep_sec for s in sleep]) or 0) / 3600 or None,
            "allenamenti": len(acts),
            "km totali": sum((a.distance_m or 0) for a in acts) / 1000 or None,
        }

    recent, before = stats(start_recent, end_recent), stats(start_before, end_before)

    lines = []
    for label in recent:
        now, then = recent[label], before[label]
        if now is None and then is None:
            continue
        delta = ""
        if now is not None and then is not None and then != 0:
            change = now - then
            arrow = "↑" if change > 0 else ("↓" if change < 0 else "=")
            delta = f"  {arrow} {abs(change):.1f} ({100 * change / then:+.0f}%)"
        lines.append(f"{label}: {_n(now, 1)} contro {_n(then, 1)}{delta}")

    return (
        f"Ultimi {days} giorni ({start_recent.strftime('%d/%m')}–"
        f"{end_recent.strftime('%d/%m')}) confrontati con i {days} precedenti "
        f"({start_before.strftime('%d/%m')}–{end_before.strftime('%d/%m')}):\n"
        + "\n".join(lines)
    )


# ============================================================================
# Registro
# ============================================================================

ToolImpl = Callable[[Session, int, dict], str]

_PERIOD_PROPS = {"date_from": _DATE_ARG, "date_to": _DATE_ARG}

TOOLS: dict[str, tuple[ToolSpec, ToolImpl]] = {
    "get_wellness": (
        ToolSpec(
            "get_wellness",
            "Passi, frequenza cardiaca a riposo, stress e Body Battery in un periodo. "
            "Su periodi lunghi restituisce medie settimanali o mensili.",
            {"type": "object", "properties": _PERIOD_PROPS},
        ),
        tool_get_wellness,
    ),
    "get_sleep": (
        ToolSpec(
            "get_sleep",
            "Durata, score e fasi del sonno in un periodo. "
            "Su periodi lunghi restituisce medie settimanali o mensili.",
            {"type": "object", "properties": _PERIOD_PROPS},
        ),
        tool_get_sleep,
    ),
    "get_activities": (
        ToolSpec(
            "get_activities",
            "Elenco degli allenamenti di un periodo, con distanza, durata, passo e FC. "
            "Ogni riga riporta l'id da usare con get_activity_detail.",
            {
                "type": "object",
                "properties": {
                    **_PERIOD_PROPS,
                    "activity_type": {
                        "type": "string",
                        "description": "Filtro sul tipo, es. running, cycling, swimming",
                    },
                },
            },
        ),
        tool_get_activities,
    ),
    "get_activity_detail": (
        ToolSpec(
            "get_activity_detail",
            "Tutti i dettagli di un singolo allenamento, dato il suo id.",
            {
                "type": "object",
                "properties": {
                    "activity_id": {"type": "integer", "description": "Id dell'allenamento"}
                },
                "required": ["activity_id"],
            },
        ),
        tool_get_activity_detail,
    ),
    "get_performance": (
        ToolSpec(
            "get_performance",
            "VO2max, carico di allenamento, HRV e training status nel tempo.",
            {"type": "object", "properties": _PERIOD_PROPS},
        ),
        tool_get_performance,
    ),
    "get_body": (
        ToolSpec(
            "get_body",
            "Peso, BMI, percentuale di grasso e massa muscolare nel tempo.",
            {"type": "object", "properties": _PERIOD_PROPS},
        ),
        tool_get_body,
    ),
    "get_goal": (
        ToolSpec(
            "get_goal",
            "L'obiettivo attivo dell'atleta con i suoi parametri e la data target.",
            {"type": "object", "properties": {}},
        ),
        tool_get_goal,
    ),
    "compare_periods": (
        ToolSpec(
            "compare_periods",
            "Confronta gli ultimi N giorni con gli N precedenti su sonno, FC a riposo, "
            "stress, passi e volume di allenamento. Da usare per le domande del tipo "
            "«sto migliorando?» o «come va rispetto al mese scorso?».",
            {
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Ampiezza di ciascun periodo in giorni (default 30)",
                    }
                },
            },
        ),
        tool_compare_periods,
    ),
}


def tool_specs() -> list[ToolSpec]:
    """Le definizioni da passare al modello."""
    return [spec for spec, _ in TOOLS.values()]


def make_executor(db: Session, user_id: int) -> Callable[[str, dict], str]:
    """Costruisce l'esecutore legato a un utente.

    L'id utente è chiuso qui dentro: il modello non può chiedere i dati di
    qualcun altro nemmeno se prova a passarlo come argomento.
    """

    def execute(name: str, arguments: dict) -> str:
        entry = TOOLS.get(name)
        if entry is None:
            return f"Strumento «{name}» inesistente."
        _, impl = entry
        logger.info("Tool %s(%s) per utente %s", name, arguments, user_id)
        return impl(db, user_id, arguments or {})

    return execute
