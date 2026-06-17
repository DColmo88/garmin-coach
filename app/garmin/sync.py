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
)
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


def _last_days(days: int) -> list[date]:
    today = date.today()
    return [today - timedelta(days=i) for i in range(days)]


def _upsert(db: Session, model, day: date):
    """Restituisce (riga, is_new) facendo lookup per giorno."""
    existing = db.scalar(select(model).where(model.day == day))
    if existing:
        return existing, False
    return model(day=day), True


# --------------------------- attività ---------------------------

def sync_activities(db: Session, limit: int = 50) -> int:
    client = get_client()
    activities = client.get_activities(0, limit) or []
    count = 0

    for act in activities:
        gid = act.get("activityId")
        if gid is None:
            continue

        existing = db.scalar(select(Activity).where(Activity.garmin_activity_id == gid))
        row = existing or Activity(garmin_activity_id=gid)

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


# --------------------------- sonno ---------------------------

def sync_sleep(db: Session, days: int = 28) -> int:
    client = get_client()
    count = 0

    for day in _last_days(days):
        cdate = day.isoformat()
        try:
            data = client.get_sleep_data(cdate)
        except Exception as exc:
            logger.warning("Sonno non disponibile per %s: %s", cdate, exc)
            continue

        dto = data.get("dailySleepDTO") if isinstance(data, dict) else None
        if not dto:
            continue

        row, is_new = _upsert(db, SleepRecord, day)
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

def sync_training(db: Session, days: int = 28) -> int:
    client = get_client()
    count = 0

    for day in _last_days(days):
        cdate = day.isoformat()

        vo2_run, vo2_bike = _fetch_vo2max(client, cdate)
        status, load = _fetch_training_status(client, cdate)
        hrv_avg, hrv_status = _fetch_hrv(client, cdate)
        readiness, readiness_lvl = _fetch_readiness(client, cdate)

        if all(v is None for v in (vo2_run, vo2_bike, status, load, hrv_avg, hrv_status, readiness)):
            continue

        row, is_new = _upsert(db, TrainingMetric, day)
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

def sync_wellness(db: Session, days: int = 28) -> int:
    client = get_client()
    count = 0

    for day in _last_days(days):
        cdate = day.isoformat()
        try:
            s = client.get_user_summary(cdate)
        except Exception as exc:
            logger.warning("Wellness non disponibile per %s: %s", cdate, exc)
            continue
        if not isinstance(s, dict) or not s:
            continue

        row, is_new = _upsert(db, DailyWellness, day)
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

def sync_body(db: Session, days: int = 90) -> int:
    client = get_client()
    end = date.today()
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

        row, is_new = _upsert(db, BodyComposition, day)
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

def sync_all(db: Session, activity_limit: int = 50, days: int = 28) -> dict[str, int]:
    """Esegue tutte le sincronizzazioni e restituisce i conteggi."""
    return {
        "activities": sync_activities(db, activity_limit),
        "wellness": sync_wellness(db, days),
        "sleep": sync_sleep(db, days),
        "training": sync_training(db, days),
        "body": sync_body(db, max(days, 90)),
    }
