"""Modelli ORM.

Due famiglie di tabelle:

- **time-series** (`activities`, `sleep_records`, `training_metrics`,
  `daily_wellness`, `body_composition`): i dati scaricati da Garmin. Ogni riga
  è identificata da `(user_id, day)` — o `(user_id, garmin_activity_id)` per le
  attività — così la ri-sincronizzazione degli stessi giorni fa upsert invece
  di duplicare, e due utenti possono avere lo stesso giorno.

- **applicative**: utenti, inviti, obiettivi, piani, cache del coaching,
  gamification, chat, consumo AI.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.database import Base

# ============================================================================
# Utenti e accesso
# ============================================================================


class User(Base):
    """Utente dell'app. Le credenziali Garmin sono anche il login."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    garmin_email: Mapped[str] = mapped_column(String, unique=True, index=True)
    garmin_password_encrypted: Mapped[str] = mapped_column(String)  # Fernet, per la sync
    garmin_password_hash: Mapped[str] = mapped_column(String)  # bcrypt, per il login
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    timezone: Mapped[str] = mapped_column(String, default="Europe/Rome")

    # Quote AI (configurabili per utente dall'admin)
    ai_quota_chat_daily: Mapped[int] = mapped_column(Integer, default=30)
    ai_quota_plans_monthly: Mapped[int] = mapped_column(Integer, default=3)

    # Preferenze notifiche: {"canale": {"tipo_evento": bool}} + collegamenti canali
    notify_prefs_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Stato sync
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_failures: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InviteCode(Base):
    """Codice invito: la registrazione è a cerchia ristretta."""

    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    used_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PushSubscription(Base):
    """Endpoint Web Push di un browser/dispositivo dell'utente."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(Text)
    p256dh: Mapped[str] = mapped_column(String)
    auth: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ============================================================================
# Time-series Garmin
# ============================================================================


class Activity(Base):
    """Una singola attività/allenamento (corsa, bici, nuoto, ...)."""

    __tablename__ = "activities"
    __table_args__ = (UniqueConstraint("user_id", "garmin_activity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    # BigInteger: gli id di Garmin hanno superato i 2^31, che su Postgres
    # e' il limite di INTEGER. Su SQLite non si notava (interi a 64 bit).
    garmin_activity_id: Mapped[int] = mapped_column(BigInteger, index=True)
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
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
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
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
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
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
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
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    weight_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_fat_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    body_water_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    muscle_mass_g: Mapped[float | None] = mapped_column(Float, nullable=True)
    bone_mass_g: Mapped[float | None] = mapped_column(Float, nullable=True)


# ============================================================================
# Obiettivi, piani, coaching
# ============================================================================


class UserGoal(Base):
    """L'obiettivo dell'utente: guida coaching, chat, piani e notifiche.

    `params_json` è libero e dipende dal tipo: per una gara
    {"target_time": "45:00", "weekly_km": 40}, per il peso {"target_weight": 72}.
    """

    __tablename__ = "user_goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    goal_type: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    params_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    priority_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    outcome: Mapped[str | None] = mapped_column(Text, nullable=True)


class TrainingPlan(Base):
    """Piano di allenamento generato dall'AI, in JSON strutturato."""

    __tablename__ = "training_plans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("user_goals.id"), nullable=True)
    plan_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    weeks_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DailyCoachCache(Base):
    """Coaching del giorno: una sola chiamata AI per utente per giorno."""

    __tablename__ = "daily_coach_cache"
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    readiness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    readiness_label: Mapped[str | None] = mapped_column(String, nullable=True)
    coach_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    workout_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    insights_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String, default="deterministic")  # o "ai"
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GamificationState(Base):
    """XP, livello, streak e badge — ricalcolati dopo ogni sync."""

    __tablename__ = "gamification_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True, index=True)
    total_xp: Mapped[int] = mapped_column(Integer, default=0)
    current_level: Mapped[str] = mapped_column(String, default="Beginner")
    current_streak: Mapped[int] = mapped_column(Integer, default=0)
    longest_streak: Mapped[int] = mapped_column(Integer, default=0)
    badges_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ============================================================================
# Chat AI
# ============================================================================


class ChatConversation(Base):
    """Una conversazione con il coach AI."""

    __tablename__ = "chat_conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String, default="Nuova conversazione")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ChatMessage(Base):
    """Un messaggio della conversazione (`role`: user | assistant)."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("chat_conversations.id"), index=True
    )
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    tools_used_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AIUsageLog(Base):
    """Consumo AI, per tenere i costi sotto controllo e applicare le quote."""

    __tablename__ = "ai_usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String)  # chat | coach | plan
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


# ============================================================================
# Notifiche
# ============================================================================


class NotificationLog(Base):
    """Notifiche inviate: serve per la deduplica (non ripetere lo stesso avviso)."""

    __tablename__ = "notification_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    channel: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
