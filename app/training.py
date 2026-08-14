"""Piani di allenamento: generazione, lettura e adattamento giornaliero.

Il piano lo scrive l'AI una volta (modello pesante, quota mensile), poi vive
nel database come JSON. L'**adattamento quotidiano** invece è deterministico:
ogni mattina si guarda la prontezza e si decide se la sessione di oggi va
tenuta, ammorbidita o spostata. Così il piano non richiede una chiamata AI
al giorno per restare sensato.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.base import AIProviderError
from app.ai.context import build_ai_context
from app.ai.provider import get_provider
from app.db.models import TrainingPlan, User, UserGoal
from app.goals import active_goal, days_to_target

logger = logging.getLogger(__name__)

WEEKDAYS = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]

# Prontezza sotto la quale una sessione dura va ammorbidita o spostata.
READINESS_SOFTEN = 50
READINESS_REST = 35

# Tipi di sessione riconosciuti, con l'icona per la UI.
SESSION_ICONS = {
    "riposo": "💤",
    "facile": "🏃",
    "lungo": "🏃",
    "ritmo": "⚡",
    "intervalli": "⚡",
    "forza": "💪",
    "crosstraining": "🚴",
}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "rationale": {"type": "string"},
        "weeks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "number": {"type": "integer"},
                    "focus": {"type": "string"},
                    "total_km": {"type": "number"},
                    "days": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "weekday": {"type": "string", "enum": WEEKDAYS},
                                "type": {
                                    "type": "string",
                                    "enum": list(SESSION_ICONS),
                                },
                                "detail": {"type": "string"},
                                "note": {"type": "string"},
                            },
                            "required": ["weekday", "type", "detail", "note"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["number", "focus", "total_km", "days"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "rationale", "weeks"],
    "additionalProperties": False,
}


class PlanError(Exception):
    """Il piano non è stato generato."""


@dataclass
class TodaySession:
    """La sessione di oggi, già adattata alla prontezza."""

    icon: str
    type: str
    detail: str
    note: str
    week_number: int
    adapted: bool = False
    adaptation_reason: str = ""


# --------------------------- accesso ---------------------------


def get_active_plan(db: Session, user_id: int) -> TrainingPlan | None:
    return db.scalar(
        select(TrainingPlan)
        .where(TrainingPlan.user_id == user_id, TrainingPlan.active.is_(True))
        .order_by(TrainingPlan.generated_at.desc())
    )


def archive_plan(db: Session, user_id: int) -> None:
    plan = get_active_plan(db, user_id)
    if plan is not None:
        plan.active = False
        db.commit()


# --------------------------- generazione ---------------------------


def _weeks_available(goal: UserGoal | None) -> int:
    """Quante settimane fino alla gara, entro limiti ragionevoli."""
    days = days_to_target(goal) if goal else None
    if days is None or days <= 0:
        return 8
    return max(2, min(days // 7, 24))


def generate_plan(db: Session, user: User) -> TrainingPlan:
    """Chiede all'AI un piano strutturato e lo salva.

    Solleva `PlanError` se non c'è un obiettivo, se la quota è esaurita o se
    il modello non produce un piano valido.
    """
    from app.ai import usage

    goal = active_goal(db, user.id)
    if goal is None:
        raise PlanError(
            "Prima serve un obiettivo: senza, un piano sarebbe generico. "
            "Impostalo nella pagina Obiettivo."
        )

    usage.check_quota(db, user, "plan")

    provider = get_provider()
    weeks = _weeks_available(goal)
    context = build_ai_context(db, user.id)

    from app.goals import describe_goal

    brief = (
        f"{describe_goal(goal)}. "
        f"Costruisci un piano di esattamente {weeks} settimane."
    )

    try:
        raw = provider.generate_training_plan(context, brief, schema=PLAN_SCHEMA)
    except TypeError:
        # Un provider che non supporta lo schema restituisce markdown: inutile
        # per il calendario, meglio dirlo che mostrare una pagina vuota.
        raise PlanError(
            "Il provider AI configurato non sa produrre un piano strutturato."
        )
    except AIProviderError as exc:
        raise PlanError(f"Il modello non ha risposto: {exc}") from exc

    data = _parse_plan(raw)
    if not data.get("weeks"):
        raise PlanError("Il piano ricevuto è vuoto. Riprova.")

    archive_plan(db, user.id)
    plan = TrainingPlan(
        user_id=user.id,
        goal_id=goal.id,
        plan_json=data,
        weeks_total=len(data["weeks"]),
        start_date=_next_monday(),
        active=True,
    )
    db.add(plan)
    usage.record(db, user.id, "plan", model=getattr(provider, "heavy", provider.name))
    db.commit()
    return plan


def _parse_plan(raw: str | dict) -> dict[str, Any]:
    """Accetta un dizionario già pronto o il JSON come testo."""
    if isinstance(raw, dict):
        return raw
    text = (raw or "").strip()
    # Alcuni modelli incorniciano il JSON in un blocco markdown.
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise PlanError("Il piano ricevuto non è leggibile.") from exc


def _next_monday(today: date | None = None) -> date:
    today = today or date.today()
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


# --------------------------- lettura del piano ---------------------------


def current_week_number(plan: TrainingPlan, today: date | None = None) -> int:
    """In che settimana del piano siamo. 1-based, limitata alla durata."""
    today = today or date.today()
    if plan.start_date is None:
        return 1
    elapsed = (today - plan.start_date).days
    if elapsed < 0:
        return 1  # il piano parte lunedì prossimo
    total = plan.weeks_total or len(plan.plan_json.get("weeks", [])) or 1
    return max(1, min(elapsed // 7 + 1, total))


def week_data(plan: TrainingPlan, number: int) -> dict[str, Any] | None:
    for week in plan.plan_json.get("weeks", []):
        if week.get("number") == number:
            return week
    weeks = plan.plan_json.get("weeks", [])
    return weeks[number - 1] if 0 < number <= len(weeks) else None


def progress_pct(plan: TrainingPlan, today: date | None = None) -> int:
    total = plan.weeks_total or len(plan.plan_json.get("weeks", [])) or 1
    return min(100, round(100 * current_week_number(plan, today) / total))


def today_session(
    plan: TrainingPlan, readiness_score: int | None, today: date | None = None
) -> TodaySession | None:
    """La sessione di oggi, adattata alla prontezza.

    L'adattamento è deterministico: sotto una certa prontezza una sessione
    dura diventa facile, molto sotto diventa riposo. Il piano non cambia —
    cambia il consiglio per oggi.
    """
    today = today or date.today()
    week = week_data(plan, current_week_number(plan, today))
    if week is None:
        return None

    weekday = WEEKDAYS[today.weekday()]
    day = next((d for d in week.get("days", []) if d.get("weekday") == weekday), None)
    if day is None:
        return None

    session_type = (day.get("type") or "facile").lower()
    session = TodaySession(
        icon=SESSION_ICONS.get(session_type, "🏃"),
        type=session_type,
        detail=day.get("detail") or "",
        note=day.get("note") or "",
        week_number=week.get("number", 1),
    )

    hard = session_type in {"intervalli", "ritmo", "lungo"}
    if readiness_score is None or session_type == "riposo" or not hard:
        return session

    if readiness_score < READINESS_REST:
        session.adapted = True
        session.adaptation_reason = (
            f"Con prontezza a {readiness_score} una sessione «{session_type}» "
            "costa più di quanto rende. Oggi riposo, la sposti a domani."
        )
        session.icon, session.type = SESSION_ICONS["riposo"], "riposo"
        session.detail = "Riposo"
    elif readiness_score < READINESS_SOFTEN:
        session.adapted = True
        session.adaptation_reason = (
            f"Prontezza a {readiness_score}: tieni la sessione ma falla facile. "
            "L'intensità la recuperi quando il corpo è pronto."
        )
        session.icon, session.type = SESSION_ICONS["facile"], "facile"
        session.detail = "Corsa facile al posto della sessione dura"

    return session
