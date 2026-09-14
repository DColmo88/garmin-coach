"""Modelli ORM.

Due famiglie di tabelle:

- **time-series** (`activities`, `sleep_records`, `training_metrics`,
  `daily_wellness`, `body_composition`): i dati scaricati da Garmin. Ogni riga
  è identificata da `(user_id, day)` — o `(user_id, source, external_id)` per le
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base

# ============================================================================
# Utenti e accesso
# ============================================================================


class User(Base):
    """Utente dell'app: un account suo, indipendente da dove arrivano i dati.

    Fino alla v2 il login *erano* le credenziali Garmin. Non reggeva più: chi
    usa Strava non ha un account Garmin da inserire, e legare l'accesso a un
    fornitore significa perderlo quando si cambia orologio. Adesso l'account è
    email + password dell'app, e la sorgente dei dati è una cosa che si collega
    dopo (vedi `ProviderConnection`).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)  # bcrypt
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    timezone: Mapped[str] = mapped_column(String, default="Europe/Rome")

    # Quante volte le sessioni di questo utente sono state invalidate.
    # Il numero finisce dentro il cookie firmato: alzarlo di uno fa decadere
    # all'istante tutti i cookie già emessi. Senza, un token firmato vale
    # trenta giorni qualunque cosa succeda dopo — cambio password compreso,
    # che è esattamente il momento in cui non deve più valere.
    session_epoch: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    # Quote AI (configurabili per utente dall'admin)
    ai_quota_chat_daily: Mapped[int] = mapped_column(Integer, default=30)
    ai_quota_plans_monthly: Mapped[int] = mapped_column(Integer, default=3)

    # Preferenze notifiche: {"canale": {"tipo_evento": bool}} + collegamenti canali
    notify_prefs_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(String, nullable=True)

    # Profilo fisiologico: le soglie che rendono confrontabili i carichi.
    # Tutte facoltative — se mancano vengono stimate dai dati osservati
    # (vedi `app.analysis.profile`), così l'app funziona anche a campi vuoti.
    # Lo sport principale: "running" | "cycling". È una preferenza di **lettura**
    # — decide cosa si vede per primo e quali primati hanno senso — non un
    # filtro sui dati: i calcoli e l'AI continuano a considerare tutto.
    primary_sport: Mapped[str | None] = mapped_column(String, nullable=True)

    sex: Mapped[str | None] = mapped_column(String, nullable=True)  # "m" | "f"
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hr_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    hr_rest: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lthr: Mapped[int | None] = mapped_column(Integer, nullable=True)  # soglia anaerobica, bpm
    ftp: Mapped[int | None] = mapped_column(Integer, nullable=True)  # potenza di soglia, watt

    # Stato sync
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_failures: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # La sorgente dei dati, al singolare perché è vincolata a una sola. Averla
    # come attributo evita di passare la sessione a chiunque debba sapere da
    # dove arrivano i dati di questo utente.
    connection: Mapped["ProviderConnection | None"] = relationship(
        "ProviderConnection", uselist=False, lazy="selectin", cascade="all, delete-orphan"
    )


class ProviderConnection(Base):
    """Da dove arrivano i dati di un utente: Garmin **oppure** Strava.

    `UniqueConstraint("user_id")` senza il provider è voluto: **una sorgente
    sola per utente**. Due sorgenti insieme vorrebbero dire la stessa corsa due
    volte — Strava importa da Garmin — e una deduplica per sovrapposizione
    temporale che sbaglierebbe a ogni cambio di fuso orario.

    Il campo `secret_encrypted` ospita due cose diverse a seconda del provider,
    ed è il motivo per cui non si chiama `password`:

    - Garmin: la password dell'account, perché la sync deve poter rifare il
      login quando il token scade;
    - Strava: il *refresh token* OAuth, che è la cosa che dura. L'access token
      vive sei ore e sta in `access_token_encrypted`.

    In nessuno dei due casi l'utente si autentica **all'app** con questo: per
    quello c'è `User.password_hash`.
    """

    __tablename__ = "provider_connections"
    __table_args__ = (UniqueConstraint("user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String)  # "garmin" | "strava"

    # Chi siamo presso il fornitore: l'email per Garmin, l'id atleta per Strava.
    external_id: Mapped[str | None] = mapped_column(String, nullable=True)

    secret_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    access_token_encrypted: Mapped[str | None] = mapped_column(String, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # "ok" | "auth_failed" | "revoked". Serve a dire in interfaccia *perché* la
    # sync non gira, invece di lasciare i dati fermi senza spiegazione.
    status: Mapped[str] = mapped_column(String, default="ok")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InviteCode(Base):
    """Codice invito: la registrazione è a cerchia ristretta."""

    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    used_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class PushSubscription(Base):
    """Endpoint Web Push di un browser/dispositivo dell'utente."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "endpoint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
    __table_args__ = (UniqueConstraint("user_id", "source", "external_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # Da quale fornitore viene la riga: "garmin" | "strava". Fa parte della
    # chiave perché gli id sono numerati da fornitori diversi e possono
    # collidere; e perché un utente che cambia sorgente conserva lo storico.
    source: Mapped[str] = mapped_column(String, default="garmin", index=True)
    # BigInteger: gli id di Garmin hanno superato i 2^31, che su Postgres
    # e' il limite di INTEGER. Su SQLite non si notava (interi a 64 bit).
    external_id: Mapped[int] = mapped_column(BigInteger, index=True)
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

    # Secondi trascorsi in ciascuna zona di frequenza cardiaca, come li calcola
    # Garmin: [z1, z2, z3, z4, z5]. È un endpoint separato (uno per attività),
    # quindi si scarica solo per le attività che non ce l'hanno ancora.
    hr_zones_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    # I **battiti** in cui ciascuna zona iniziava, presi dalla stessa risposta
    # che porta i secondi. Senza, l'app mostrava confini calcolati come
    # percentuali della FC massima mentre i secondi erano stati contati con le
    # zone configurate sull'orologio — che possono essere impostate su riserva
    # cardiaca o su soglia. I numeri a schermo e quelli sotto il grafico
    # potevano quindi non essere gli stessi.
    hr_zone_bounds_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)

    # Quanto è costata **secondo chi l'ha fatta**, sulla scala di Borg CR10.
    # È l'unico campo di questa tabella che non arriva da un fornitore, e sta
    # qui invece che in una tabella sua per una ragione pratica: `activity_load`
    # lo legge per ogni attività di centottanta giorni, e una join in quel
    # punto si sentirebbe. La sync scrive campo per campo, quindi non lo tocca.
    rpe: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feel_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class DailyCheckin(Base):
    """Come sta l'atleta, secondo l'atleta. Una riga per giorno.

    È l'unico dato dell'app che non viene misurato da un apparecchio, ed è
    quello che in letteratura predice l'affaticamento meglio di HRV e
    frequenza a riposo. Costa quindici secondi al mattino e risolve due
    problemi che nessun sensore risolve:

    - chi usa Strava non ha **niente** di notturno: senza questo, tre dei
      fattori di prontezza su sette restano vuoti per sempre;
    - un orologio non sa che hai i bambini malati, che sei in viaggio o che
      hai le gambe piene da ieri.

    I valori vanno da 1 a 5 e sono tutti orientati allo stesso modo — **più
    alto è meglio** — perché una scala che si inverte da un campo all'altro è
    una scala che qualcuno compila al contrario.
    """

    __tablename__ = "daily_checkins"
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)

    energy: Mapped[int | None] = mapped_column(Integer, nullable=True)        # 1-5
    legs: Mapped[int | None] = mapped_column(Integer, nullable=True)          # 1-5, 5 = fresche
    mood: Mapped[int | None] = mapped_column(Integer, nullable=True)          # 1-5
    sleep_quality: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class SleepRecord(Base):
    """Riepilogo del sonno di una notte (indicizzato per giorno)."""

    __tablename__ = "sleep_records"
    __table_args__ = (UniqueConstraint("user_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    goal_id: Mapped[int | None] = mapped_column(ForeignKey("user_goals.id", ondelete="SET NULL"), nullable=True)
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
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date, index=True)
    readiness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    readiness_label: Mapped[str | None] = mapped_column(String, nullable=True)
    coach_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    workout_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    insights_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    # Verdetto e azione scritti dall'AI per Sonno, Recupero, Corpo e Forma:
    # `{"sleep": {"verdict": ..., "action": ...}, ...}`. Stanno qui e non in
    # una tabella loro perché hanno esattamente la stessa vita del resto della
    # riga — un giorno, un utente, una chiamata al modello.
    readings_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String, default="deterministic")  # o "ai"
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class GamificationState(Base):
    """XP, livello, streak e badge — ricalcolati dopo ogni sync."""

    __tablename__ = "gamification_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
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
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
        ForeignKey("chat_conversations.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String)
    content: Mapped[str] = mapped_column(Text)
    tools_used_json: Mapped[list[Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AIUsageLog(Base):
    """Consumo AI, per tenere i costi sotto controllo e applicare le quote."""

    __tablename__ = "ai_usage_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
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
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String, index=True)
    channel: Mapped[str] = mapped_column(String)
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
