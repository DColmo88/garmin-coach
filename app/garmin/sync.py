"""Sincronizzazione: scarica i dati da Garmin e li salva nel database.

Le risposte di Garmin sono dizionari con strutture che possono variare:
tutto il parsing è difensivo (`.get(...)` con fallback su più chiavi), così
un campo mancante non interrompe la sincronizzazione.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Activity,
    BodyComposition,
    DailyWellness,
    SleepRecord,
    TrainingMetric,
    User,
)
from app.clock import today_for
from app.garmin.client import get_client

logger = logging.getLogger(__name__)


# --------------------------- helper di parsing ---------------------------

def _dig(data: Any, *keys: str) -> Any:
    cur = data
    for key in keys:
        if isinstance(cur, dict):
            cur = cur.get(key)
        else:
            return None
    return cur


def _first(data: dict | None, *keys: str) -> Any:
    """Primo valore non nullo tra più chiavi alternative."""
    if not isinstance(data, dict):
        return None
    for k in keys:
        if data.get(k) is not None:
            return data[k]
    return None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _last_days(days: int, user: User) -> list[date]:
    """Gli ultimi `days` giorni, contati dal calendario dell'utente."""
    today = today_for(user)
    return [today - timedelta(days=i) for i in range(days)]


# Quanti giorni recenti si riscaricano comunque, anche se già in archivio.
# Garmin corregge il sonno e il riepilogo quotidiano per un paio di giorni
# dopo — l'orologio sincronizza in ritardo, l'utente modifica a mano — quindi
# le ultime giornate vanno riprese sempre.
REFRESH_DAYS = 4


def _days_to_fetch(db: Session, model, user: User, days: int) -> list[date]:
    """I giorni da chiedere davvero a Garmin: i mancanti più i più recenti.

    È la differenza fra una sincronizzazione notturna da centosettanta
    richieste e una da una quindicina.

    La versione precedente riscaricava ventotto giorni per tre endpoint —
    e `sync_training` ne interroga quattro per giorno, quindi centododici
    richieste da sole — **ogni notte**, riscrivendo con gli stessi valori righe
    che erano già in archivio e che non sarebbero più cambiate. Per un'API non
    documentata e legata a un account personale, il volume è il rischio
    operativo principale che questa app abbia.

    Cosa si chiede: i giorni che nel database non ci sono, più gli ultimi
    `REFRESH_DAYS` a prescindere. I secondi perché Garmin li ritocca ancora.
    """
    window = _last_days(days, user)
    oldest = min(window)

    known = set(db.scalars(
        select(model.day).where(model.user_id == user.id, model.day >= oldest)
    ).all())

    recent = set(_last_days(REFRESH_DAYS, user))
    wanted = [d for d in window if d not in known or d in recent]
    logger.info(
        "%s: %d giorni da scaricare su %d (%d già in archivio).",
        model.__tablename__, len(wanted), len(window), len(known),
    )
    return wanted


def _upsert(db: Session, model, user_id: int, day: date):
    """Restituisce (riga, is_new) cercando la riga di QUEL utente in QUEL giorno."""
    existing = db.scalar(
        select(model).where(model.user_id == user_id, model.day == day)
    )
    if existing:
        return existing, False
    return model(user_id=user_id, day=day), True


# --------------------------- attività ---------------------------

# Quanto indietro si va la prima volta che si collega un account.
# Centottanta giorni sono la finestra su cui `app/analysis/` calcola le curve
# di fitness e fatica: con meno, la curva parte da una stima e i primi
# quarantadue giorni sono un artefatto. Trecentosessantacinque è quello che
# Strava scarica al primo collegamento, e le due sorgenti devono partire alla
# pari.
FIRST_SYNC_DAYS = 365

# Quante attività per pagina e quante pagine al massimo. Il tetto esiste
# perché un account con dieci anni di storia non deve poter trasformare il
# primo collegamento in mille richieste.
ACTIVITY_PAGE = 50
MAX_ACTIVITY_PAGES = 10

# Quanti giorni già visti si riprendono a ogni sync: capita di rinominare o
# correggere un'attività dopo averla caricata.
ACTIVITY_OVERLAP_DAYS = 3


def _activity_horizon(db: Session, user: User) -> date:
    """Fin dove indietro serve scaricare le attività.

    Al primo collegamento un anno intero; poi soltanto da poco prima
    dell'ultima già in archivio. Prima si chiedevano sempre e solo le ultime
    cinquanta e basta: chi collegava Garmin con anni di storia ne otteneva
    cinquanta — due mesi scarsi per chi si allena spesso — e le curve di
    fitness partivano da un seme inventato. Chi invece stava due mesi senza
    aprire l'app perdeva per sempre le attività oltre la cinquantesima.
    """
    today = today_for(user)
    newest = db.scalar(
        select(Activity.start_time)
        .where(Activity.user_id == user.id, Activity.source == "garmin")
        .order_by(Activity.start_time.desc())
        .limit(1)
    )
    if newest is None:
        return today - timedelta(days=FIRST_SYNC_DAYS)
    return newest.date() - timedelta(days=ACTIVITY_OVERLAP_DAYS)


def _fetch_activities(client, horizon: date) -> list[dict]:
    """Pagina all'indietro finché non si supera l'orizzonte."""
    collected: list[dict] = []
    for page in range(MAX_ACTIVITY_PAGES):
        batch = client.get_activities(page * ACTIVITY_PAGE, ACTIVITY_PAGE) or []
        if not batch:
            break
        collected.extend(batch)

        oldest = _parse_dt(batch[-1].get("startTimeLocal"))
        if oldest is None or oldest.date() <= horizon:
            break
        if len(batch) < ACTIVITY_PAGE:
            break
    return collected


def sync_activities(db: Session, user: User, limit: int = ACTIVITY_PAGE) -> int:
    client = get_client(user)
    horizon = _activity_horizon(db, user)
    activities = _fetch_activities(client, horizon)
    count = 0

    for act in activities:
        gid = act.get("activityId")
        if gid is None:
            continue

        existing = db.scalar(
            select(Activity).where(
                Activity.user_id == user.id, Activity.source == "garmin", Activity.external_id == gid
            )
        )
        row = existing or Activity(user_id=user.id, source="garmin", external_id=gid)

        row.name = act.get("activityName")
        row.activity_type = _dig(act, "activityType", "typeKey")
        row.start_time = _parse_dt(act.get("startTimeLocal"))
        row.duration_sec = act.get("duration")
        row.distance_m = act.get("distance")
        row.avg_hr = act.get("averageHR")
        row.max_hr = act.get("maxHR")
        row.avg_speed = act.get("averageSpeed")
        row.calories = act.get("calories")
        row.elevation_gain_m = act.get("elevationGain")
        row.avg_cadence = _first(act, "averageRunningCadenceInStepsPerMinute", "averageBikingCadenceInRevPerMinute")
        row.avg_power = act.get("avgPower")
        row.aerobic_te = act.get("aerobicTrainingEffect")
        row.anaerobic_te = act.get("anaerobicTrainingEffect")

        if existing is None:
            db.add(row)
        count += 1

    db.commit()
    logger.info("Sincronizzate %d attività.", count)
    return count


# --------------------------- zone di frequenza cardiaca ---------------------------

# Il tempo per zona è un endpoint separato: una chiamata per attività. Si
# scarica solo per le attività che non ce l'hanno ancora, e con un tetto per
# sync, così la prima sincronizzazione non fa duecento richieste di fila.
ZONES_PER_SYNC = 25


def sync_activity_zones(db: Session, user: User, limit: int = ZONES_PER_SYNC) -> int:
    """Scarica il tempo trascorso in ogni zona FC per le attività che ne sono prive.

    È il dato che permette di dire com'è distribuita davvero l'intensità: la FC
    media di un'intera uscita non distingue un fondo lento da un fartlek.
    """
    pending = list(db.scalars(
        select(Activity)
        .where(
            Activity.user_id == user.id,
            Activity.hr_zones_json.is_(None),
            Activity.avg_hr.isnot(None),
        )
        .order_by(Activity.start_time.desc())
        .limit(limit)
    ).all())
    if not pending:
        return 0

    client = get_client(user)
    count = 0
    for activity in pending:
        try:
            data = client.get_activity_hr_in_timezones(activity.external_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Zone FC non disponibili per %s: %s",
                           activity.external_id, exc)
            continue

        zones, bounds = _parse_hr_zones(data)
        if zones is None:
            continue
        activity.hr_zones_json = zones
        activity.hr_zone_bounds_json = bounds
        count += 1

    db.commit()
    logger.info("Scaricate le zone FC di %d attività.", count)
    return count


def _parse_hr_zones(data: Any) -> tuple[list[float] | None, list[float] | None]:
    """Secondi per zona **e** i battiti in cui ogni zona comincia.

    Garmin restituisce una voce per zona con `zoneNumber`, `secsInZone` e
    `zoneLowBoundary`, ma salta le zone in cui non si è passato tempo: qui si
    riempiono di zero, perché a valle serve sempre un vettore di cinque valori.

    I confini si salvano insieme ai secondi perché arrivano dalla stessa
    risposta e descrivono **come quei secondi sono stati contati**. L'app fino
    a qui li ricalcolava per conto suo come percentuali della FC massima,
    mentre l'orologio può avere le zone tarate su riserva cardiaca o su soglia:
    il grafico contava secondo una scala e la legenda ne dichiarava un'altra.
    """
    if not isinstance(data, list) or not data:
        return None, None

    zones = [0.0] * 5
    bounds: list[float | None] = [None] * 5
    found = False
    for entry in data:
        if not isinstance(entry, dict):
            continue
        number = _first(entry, "zoneNumber", "zoneNo")
        seconds = _first(entry, "secsInZone", "secondsInZone")
        if number is None or seconds is None:
            continue
        index = int(number) - 1
        if 0 <= index < len(zones):
            zones[index] = float(seconds)
            low = _first(entry, "zoneLowBoundary", "lowBoundary")
            if low is not None:
                bounds[index] = float(low)
            found = True

    if not found:
        return None, None
    return zones, (bounds if any(b is not None for b in bounds) else None)


# --------------------------- sonno ---------------------------

def sync_sleep(db: Session, user: User, days: int = 28) -> int:
    client = get_client(user)
    count = 0

    for day in _days_to_fetch(db, SleepRecord, user, days):
        cdate = day.isoformat()
        try:
            data = client.get_sleep_data(cdate)
        except Exception as exc:
            logger.warning("Sonno non disponibile per %s: %s", cdate, exc)
            continue

        dto = data.get("dailySleepDTO") if isinstance(data, dict) else None
        if not dto:
            continue

        row, is_new = _upsert(db, SleepRecord, user.id, day)
        row.total_sleep_sec = dto.get("sleepTimeSeconds")
        row.deep_sleep_sec = dto.get("deepSleepSeconds")
        row.light_sleep_sec = dto.get("lightSleepSeconds")
        row.rem_sleep_sec = dto.get("remSleepSeconds")
        row.awake_sec = dto.get("awakeSleepSeconds")
        row.sleep_score = _dig(dto, "sleepScores", "overall", "value")
        row.resting_hr = data.get("restingHeartRate")
        row.avg_spo2 = _first(data, "averageSpO2Value", "averageSpO2")
        row.avg_respiration = _first(data, "avgSleepRespirationValue", "averageRespirationValue")

        if is_new:
            db.add(row)
        count += 1

    db.commit()
    logger.info("Sincronizzati %d giorni di sonno.", count)
    return count


# --------------------------- training / performance ---------------------------

def sync_training(db: Session, user: User, days: int = 28) -> int:
    client = get_client(user)
    count = 0

    for day in _days_to_fetch(db, TrainingMetric, user, days):
        cdate = day.isoformat()

        vo2_run, vo2_bike = _fetch_vo2max(client, cdate)
        status, load = _fetch_training_status(client, cdate)
        hrv_avg, hrv_status = _fetch_hrv(client, cdate)
        readiness, readiness_lvl = _fetch_readiness(client, cdate)

        if all(v is None for v in (vo2_run, vo2_bike, status, load, hrv_avg, hrv_status, readiness)):
            continue

        row, is_new = _upsert(db, TrainingMetric, user.id, day)
        row.vo2max = vo2_run
        row.vo2max_cycling = vo2_bike
        row.training_status = status
        row.training_load = load
        row.hrv_weekly_avg = hrv_avg
        row.hrv_status = hrv_status
        row.readiness_score = readiness
        row.readiness_level = readiness_lvl

        if is_new:
            db.add(row)
        count += 1

    db.commit()
    logger.info("Sincronizzati %d giorni di metriche training.", count)
    return count


def _fetch_vo2max(client, cdate: str) -> tuple[float | None, float | None]:
    try:
        data = client.get_max_metrics(cdate)
    except Exception:
        return None, None
    if isinstance(data, list) and data:
        data = data[0]
    run = _dig(data, "generic", "vo2MaxPreciseValue") or _dig(data, "generic", "vo2MaxValue")
    bike = _dig(data, "cycling", "vo2MaxPreciseValue") or _dig(data, "cycling", "vo2MaxValue")
    return run, bike


def _fetch_training_status(client, cdate: str) -> tuple[str | None, float | None]:
    try:
        data = client.get_training_status(cdate)
    except Exception:
        return None, None
    latest = _dig(data, "mostRecentTrainingStatus", "latestTrainingStatusData")
    if isinstance(latest, dict) and latest:
        entry = next(iter(latest.values()), {})
        status = _first(entry, "trainingStatusFeedbackPhrase", "trainingStatus")
        load = _first(entry, "weeklyTrainingLoad", "loadTunnelMax")
        return (str(status) if status is not None else None), load
    return None, None


def _fetch_hrv(client, cdate: str) -> tuple[float | None, str | None]:
    try:
        data = client.get_hrv_data(cdate)
    except Exception:
        return None, None
    return _dig(data, "hrvSummary", "weeklyAvg"), _dig(data, "hrvSummary", "status")


def _fetch_readiness(client, cdate: str) -> tuple[float | None, str | None]:
    try:
        data = client.get_training_readiness(cdate)
    except Exception:
        return None, None
    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        return None, None
    return _first(data, "score"), _first(data, "level")


# --------------------------- wellness quotidiano ---------------------------

def sync_wellness(db: Session, user: User, days: int = 28) -> int:
    client = get_client(user)
    count = 0

    for day in _days_to_fetch(db, DailyWellness, user, days):
        cdate = day.isoformat()
        try:
            s = client.get_user_summary(cdate)
        except Exception as exc:
            logger.warning("Wellness non disponibile per %s: %s", cdate, exc)
            continue
        if not isinstance(s, dict) or not s:
            continue

        row, is_new = _upsert(db, DailyWellness, user.id, day)
        row.total_steps = _first(s, "totalSteps")
        row.step_goal = _first(s, "dailyStepGoal", "stepGoal")
        row.total_distance_m = _first(s, "totalDistanceMeters", "totalDistance")
        row.floors_up = _first(s, "floorsAscended")
        row.floors_goal = _first(s, "userFloorsAscendedGoal", "floorsAscendedGoal")
        row.resting_hr = _first(s, "restingHeartRate")
        row.min_hr = _first(s, "minHeartRate")
        row.max_hr = _first(s, "maxHeartRate")
        row.avg_stress = _first(s, "averageStressLevel")
        row.max_stress = _first(s, "maxStressLevel")
        row.body_battery_high = _first(s, "bodyBatteryHighestValue", "highestBatteryLevel")
        row.body_battery_low = _first(s, "bodyBatteryLowestValue", "lowestBatteryLevel")
        row.avg_spo2 = _first(s, "averageSpo2", "averageSpO2Value")
        row.avg_respiration = _first(s, "avgWakingRespirationValue", "avgRespirationValue")
        row.moderate_intensity_min = _first(s, "moderateIntensityMinutes")
        row.vigorous_intensity_min = _first(s, "vigorousIntensityMinutes")
        row.total_calories = _first(s, "totalKilocalories")
        row.active_calories = _first(s, "activeKilocalories")
        row.bmr_calories = _first(s, "bmrKilocalories")

        if is_new:
            db.add(row)
        count += 1

    db.commit()
    logger.info("Sincronizzati %d giorni di wellness.", count)
    return count


# --------------------------- composizione corporea ---------------------------

def sync_body(db: Session, user: User, days: int = 90) -> int:
    client = get_client(user)
    end = today_for(user)
    start = end - timedelta(days=days)
    try:
        data = client.get_body_composition(start.isoformat(), end.isoformat())
    except Exception as exc:
        logger.warning("Composizione corporea non disponibile: %s", exc)
        return 0

    entries = _dig(data, "dateWeightList") or []
    count = 0
    for e in entries:
        cal = _first(e, "calendarDate", "date")
        if not cal:
            continue
        try:
            day = date.fromisoformat(str(cal)[:10])
        except ValueError:
            continue

        row, is_new = _upsert(db, BodyComposition, user.id, day)
        row.weight_g = _first(e, "weight")
        row.bmi = _first(e, "bmi")
        row.body_fat_pct = _first(e, "bodyFat")
        row.body_water_pct = _first(e, "bodyWater")
        row.muscle_mass_g = _first(e, "muscleMass")
        row.bone_mass_g = _first(e, "boneMass")

        if is_new:
            db.add(row)
        count += 1

    db.commit()
    logger.info("Sincronizzate %d misurazioni corporee.", count)
    return count


# --------------------------- sync completa ---------------------------

def sync_all(
    db: Session, user: User, activity_limit: int = 50, days: int = 28
) -> dict[str, int]:
    """Esegue tutte le sincronizzazioni per un utente e restituisce i conteggi."""
    return {
        "activities": sync_activities(db, user, activity_limit),
        "zones": sync_activity_zones(db, user),
        "wellness": sync_wellness(db, user, days),
        "sleep": sync_sleep(db, user, days),
        "training": sync_training(db, user, days),
        "body": sync_body(db, user, max(days, 90)),
    }
