"""Modelli ORM per i dati Garmin.

Ogni tabella time-series ha un campo `day` (o `garmin_activity_id`) unico,
così la risincronizzazione degli stessi giorni fa upsert invece di duplicare.
"""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base


class Activity(Base):
    """Una singola attività/allenamento (corsa, bici, nuoto, ...)."""

    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    garmin_activity_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String, nullable=True)
    activity_type: Mapped[str | None] = mapped_column(String, nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, index=True)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_speed: Mapped[float | None] = mapped_column(Float, nullable=True)
    calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_cadence: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_power: Mapped[float | None] = mapped_column(Float, nullable=True)
    aerobic_te: Mapped[float | None] = mapped_column(Float, nullable=True)
    anaerobic_te: Mapped[float | None] = mapped_column(Float, nullable=True)


class SleepRecord(Base):
    """Riepilogo del sonno di una notte (indicizzato per giorno)."""

    __tablename__ = "sleep_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True, index=True)
    total_sleep_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    deep_sleep_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    light_sleep_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    rem_sleep_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    awake_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    sleep_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_spo2: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_respiration: Mapped[float | None] = mapped_column(Float, nullable=True)


class TrainingMetric(Base):
    """Metriche di performance/training per giorno."""

    __tablename__ = "training_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True, index=True)
    vo2max: Mapped[float | None] = mapped_column(Float, nullable=True)
    vo2max_cycling: Mapped[float | None] = mapped_column(Float, nullable=True)
    training_status: Mapped[str | None] = mapped_column(String, nullable=True)
    training_load: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_weekly_avg: Mapped[float | None] = mapped_column(Float, nullable=True)
    hrv_status: Mapped[str | None] = mapped_column(String, nullable=True)
    readiness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    readiness_level: Mapped[str | None] = mapped_column(String, nullable=True)


class DailyWellness(Base):
    """Riepilogo salute quotidiano (da get_user_summary): passi, FC, stress, ecc."""

    __tablename__ = "daily_wellness"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True, index=True)
    total_steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    step_goal: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_distance_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    floors_up: Mapped[float | None] = mapped_column(Float, nullable=True)
    floors_goal: Mapped[float | None] = mapped_column(Float, nullable=True)
    resting_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    min_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_stress: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_stress: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_battery_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_battery_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_spo2: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_respiration: Mapped[float | None] = mapped_column(Float, nullable=True)
    moderate_intensity_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    vigorous_intensity_min: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    active_calories: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmr_calories: Mapped[float | None] = mapped_column(Float, nullable=True)


class BodyComposition(Base):
    """Misurazioni del corpo (da get_body_composition / weigh-ins)."""

    __tablename__ = "body_composition"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day: Mapped[date] = mapped_column(Date, unique=True, index=True)
    weight_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_fat_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_water_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    muscle_mass_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    bone_mass_g: Mapped[float | None] = mapped_column(Float, nullable=True)
