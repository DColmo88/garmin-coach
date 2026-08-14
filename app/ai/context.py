"""Costruisce il contesto strutturato dei dati Garmin da passare all'AI."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app import queries as q


def build_ai_context(db: Session, user_id: int, days: int = 28) -> dict[str, Any]:
    """Raccoglie tutti i dati recenti di un utente in un dizionario per l'AI."""
    activities = q.recent_activities(db, user_id, 20)
    wellness = q.wellness_series(db, user_id, days)
    sleep = q.sleep_series(db, user_id, days)
    training = q.training_series(db, user_id, days)
    body = q.body_series(db, user_id, days)

    return {
        "period_days": days,
        "activities": [
            {
                "name": a.name,
                "type": a.activity_type,
                "start_time": a.start_time.isoformat() if a.start_time else None,
                "duration_sec": a.duration_sec,
                "distance_m": a.distance_m,
                "avg_hr": a.avg_hr,
                "max_hr": a.max_hr,
                "avg_speed": a.avg_speed,
                "elevation_gain_m": a.elevation_gain_m,
                "aerobic_te": a.aerobic_te,
                "anaerobic_te": a.anaerobic_te,
                "calories": a.calories,
            }
            for a in activities
        ],
        "wellness": [
            {
                "day": w.day.isoformat(),
                "steps": w.total_steps,
                "resting_hr": w.resting_hr,
                "avg_stress": w.avg_stress,
                "body_battery_high": w.body_battery_high,
                "body_battery_low": w.body_battery_low,
                "avg_spo2": w.avg_spo2,
                "intensity_min": (w.moderate_intensity_min or 0) + (w.vigorous_intensity_min or 0),
                "active_calories": w.active_calories,
            }
            for w in wellness
        ],
        "sleep": [
            {
                "day": s.day.isoformat(),
                "total_sleep_sec": s.total_sleep_sec,
                "deep_sleep_sec": s.deep_sleep_sec,
                "rem_sleep_sec": s.rem_sleep_sec,
                "sleep_score": s.sleep_score,
                "resting_hr": s.resting_hr,
            }
            for s in sleep
        ],
        "training": [
            {
                "day": t.day.isoformat(),
                "vo2max": t.vo2max,
                "training_status": t.training_status,
                "training_load": t.training_load,
                "hrv_weekly_avg": t.hrv_weekly_avg,
                "hrv_status": t.hrv_status,
                "readiness_score": t.readiness_score,
            }
            for t in training
        ],
        "body": [
            {
                "day": b.day.isoformat(),
                "weight_kg": (b.weight_g / 1000) if b.weight_g else None,
                "bmi": b.bmi,
                "body_fat_pct": b.body_fat_pct,
                "muscle_mass_kg": (b.muscle_mass_g / 1000) if b.muscle_mass_g else None,
            }
            for b in body
        ],
    }
