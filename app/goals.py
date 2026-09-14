"""Obiettivi dell'utente — il "laboratorio".

Ogni tipo di obiettivo dichiara qui i propri parametri: la UI genera il form
da questa definizione e il resto dell'app (coaching, chat, piani, notifiche)
legge l'obiettivo attivo senza sapere nulla dei singoli tipi.

Aggiungere un obiettivo nuovo = aggiungere una voce a GOAL_TYPES.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User, UserGoal


@dataclass(frozen=True)
class GoalParam:
    """Un parametro configurabile di un obiettivo."""

    key: str
    label: str
    kind: Literal["number", "text", "time", "select"]
    unit: str | None = None
    placeholder: str | None = None
    options: tuple[str, ...] = ()
    help: str | None = None


@dataclass(frozen=True)
class GoalType:
    """Un tipo di obiettivo selezionabile."""

    key: str
    icon: str
    title: str
    subtitle: str
    wants_target_date: bool = True
    date_label: str = "Data della gara"
    params: tuple[GoalParam, ...] = field(default_factory=tuple)


_PACE_HELP = "Lascia vuoto se non hai un tempo preciso in mente."

GOAL_TYPES: tuple[GoalType, ...] = (
    GoalType(
        key="race_5k", icon="🏃", title="Gara 5K",
        subtitle="Preparare una 5 km, dalla prima volta al personale",
        params=(
            GoalParam("target_time", "Tempo obiettivo", "text",
                      placeholder="es. 25:00", help=_PACE_HELP),
            GoalParam("weekly_km", "Chilometri a settimana", "number", unit="km",
                      placeholder="es. 25"),
        ),
    ),
    GoalType(
        key="race_10k", icon="🏃", title="Gara 10K",
        subtitle="Il classico banco di prova su strada",
        params=(
            GoalParam("target_time", "Tempo obiettivo", "text",
                      placeholder="es. 50:00", help=_PACE_HELP),
            GoalParam("weekly_km", "Chilometri a settimana", "number", unit="km",
                      placeholder="es. 35"),
        ),
    ),
    GoalType(
        key="race_half", icon="🏅", title="Mezza maratona",
        subtitle="21,097 km — volume e resistenza",
        params=(
            GoalParam("target_time", "Tempo obiettivo", "text",
                      placeholder="es. 1:45:00", help=_PACE_HELP),
            GoalParam("weekly_km", "Chilometri a settimana", "number", unit="km",
                      placeholder="es. 45"),
            GoalParam("longest_run_km", "Lunghissimo attuale", "number", unit="km",
                      placeholder="es. 15"),
        ),
    ),
    GoalType(
        key="race_marathon", icon="🏆", title="Maratona",
        subtitle="42,195 km — il progetto lungo",
        params=(
            GoalParam("target_time", "Tempo obiettivo", "text",
                      placeholder="es. 3:45:00", help=_PACE_HELP),
            GoalParam("weekly_km", "Chilometri a settimana", "number", unit="km",
                      placeholder="es. 60"),
            GoalParam("longest_run_km", "Lunghissimo attuale", "number", unit="km",
                      placeholder="es. 25"),
        ),
    ),
    GoalType(
        key="fitness", icon="💪", title="Forma generale",
        subtitle="Stare meglio, senza una gara in calendario",
        wants_target_date=False,
        params=(
            GoalParam("sessions_per_week", "Allenamenti a settimana", "number",
                      placeholder="es. 4"),
            GoalParam("focus", "Priorità", "select",
                      options=("Resistenza", "Forza", "Perdere peso", "Equilibrio generale")),
        ),
    ),
    GoalType(
        key="weight", icon="⚖️", title="Peso forma",
        subtitle="Ricomposizione corporea sostenibile",
        date_label="Entro quando (facoltativo)",
        params=(
            GoalParam("target_weight", "Peso obiettivo", "number", unit="kg",
                      placeholder="es. 72"),
            GoalParam("weekly_rate", "Ritmo settimanale", "number", unit="kg/sett.",
                      placeholder="es. 0.4",
                      help="Oltre 0,7 kg a settimana si perde anche massa muscolare."),
        ),
    ),
    GoalType(
        key="sleep", icon="🌙", title="Dormire meglio",
        subtitle="Recupero notturno come priorità",
        wants_target_date=False,
        params=(
            GoalParam("target_hours", "Ore di sonno obiettivo", "number", unit="h",
                      placeholder="es. 7.5"),
            GoalParam("bedtime", "Ora in cui vuoi essere a letto", "time"),
        ),
    ),
    GoalType(
        key="recovery", icon="🌱", title="Recupero",
        subtitle="Rientro dopo infortunio, gara o stop prolungato",
        date_label="Rientro previsto (facoltativo)",
        params=(
            GoalParam("what_happened", "Da cosa stai rientrando", "text",
                      placeholder="es. tendinite al ginocchio, maratona, influenza"),
            GoalParam("weeks_off", "Settimane di stop", "number", unit="sett.",
                      placeholder="es. 3"),
        ),
    ),
    GoalType(
        key="custom", icon="🎯", title="Obiettivo mio",
        subtitle="Qualcosa che non rientra negli altri",
        date_label="Entro quando (facoltativo)",
        params=(
            GoalParam("description", "Descrivi l'obiettivo", "text",
                      placeholder="es. correre 100 km in un mese"),
        ),
    ),
)

GOAL_TYPES_BY_KEY: dict[str, GoalType] = {g.key: g for g in GOAL_TYPES}


def get_goal_type(key: str) -> GoalType | None:
    return GOAL_TYPES_BY_KEY.get(key)


# --------------------------- accesso al DB ---------------------------

def active_goal(db: Session, user_id: int) -> UserGoal | None:
    """L'obiettivo attivo dell'utente, o None se non ne ha impostato uno."""
    return db.scalar(
        select(UserGoal)
        .where(UserGoal.user_id == user_id, UserGoal.active.is_(True))
        .order_by(UserGoal.created_at.desc())
    )


def past_goals(db: Session, user_id: int) -> list[UserGoal]:
    """Obiettivi archiviati, dal più recente."""
    return list(
        db.scalars(
            select(UserGoal)
            .where(UserGoal.user_id == user_id, UserGoal.active.is_(False))
            .order_by(UserGoal.closed_at.desc(), UserGoal.created_at.desc())
        ).all()
    )


def clean_params(goal_type: GoalType, raw: dict[str, Any]) -> dict[str, Any]:
    """Tiene solo i parametri previsti dal tipo, convertendo i numeri."""
    out: dict[str, Any] = {}
    for param in goal_type.params:
        value = raw.get(param.key)
        if value is None or str(value).strip() == "":
            continue
        if param.kind == "number":
            try:
                out[param.key] = float(str(value).replace(",", "."))
            except ValueError:
                continue
        else:
            out[param.key] = str(value).strip()
    return out


def set_active_goal(
    db: Session,
    user: User,
    goal_type_key: str,
    *,
    title: str | None = None,
    target_date: date | None = None,
    params: dict[str, Any] | None = None,
    priority_notes: str | None = None,
) -> UserGoal:
    """Archivia l'obiettivo attivo (se c'è) e ne imposta uno nuovo.

    Un solo obiettivo alla volta: così coaching, chat e piani non devono mai
    scegliere a quale dare retta.
    """
    goal_type = get_goal_type(goal_type_key)
    if goal_type is None:
        raise ValueError(f"Tipo di obiettivo sconosciuto: {goal_type_key}")

    from datetime import datetime

    current = active_goal(db, user.id)
    if current is not None:
        current.active = False
        current.closed_at = datetime.utcnow()

    goal = UserGoal(
        user_id=user.id,
        goal_type=goal_type.key,
        title=(title or "").strip() or goal_type.title,
        target_date=target_date if goal_type.wants_target_date or target_date else None,
        params_json=clean_params(goal_type, params or {}),
        priority_notes=(priority_notes or "").strip() or None,
        active=True,
    )
    db.add(goal)
    db.commit()
    return goal


def close_goal(db: Session, user_id: int, outcome: str | None = None) -> UserGoal | None:
    """Archivia l'obiettivo attivo, con una nota facoltativa sull'esito."""
    from datetime import datetime

    goal = active_goal(db, user_id)
    if goal is None:
        return None
    goal.active = False
    goal.closed_at = datetime.utcnow()
    goal.outcome = (outcome or "").strip() or None
    db.commit()
    return goal


# --------------------------- sintesi per l'AI ---------------------------

def days_to_target(goal: UserGoal, today: date | None = None) -> int | None:
    """Quanti giorni mancano alla data obiettivo.

    `today` è quello dell'atleta quando il chiamante ce l'ha; senza,
    ricade sul server. Su un conto alla rovescia una giornata di scarto si
    vede, ed è la settimana di scarico a decidersi su quel numero.
    """
    if goal.target_date is None:
        return None
    return (goal.target_date - (today or _goal_today(goal))).days


def _goal_today(goal: UserGoal) -> date:
    from sqlalchemy.orm import object_session

    from app.clock import today_for

    session = object_session(goal)
    owner = session.get(User, goal.user_id) if session is not None else None
    return today_for(owner) if owner is not None else date.today()


def describe_goal(goal: UserGoal | None, today: date | None = None) -> str:
    """Una riga leggibile da mettere nei prompt. Compatta: costa token."""
    if goal is None:
        return "Nessun obiettivo impostato."

    goal_type = get_goal_type(goal.goal_type)
    parts = [goal.title or (goal_type.title if goal_type else goal.goal_type)]

    for key, value in (goal.params_json or {}).items():
        label = key
        unit = ""
        if goal_type:
            match = next((p for p in goal_type.params if p.key == key), None)
            if match:
                label = match.label.lower()
                unit = f" {match.unit}" if match.unit else ""
        pretty = f"{value:g}" if isinstance(value, float) else value
        parts.append(f"{label}: {pretty}{unit}")

    days = days_to_target(goal, today)
    if days is not None:
        if days > 0:
            parts.append(f"mancano {days} giorni ({goal.target_date.strftime('%d/%m/%Y')})")
        elif days == 0:
            parts.append("è oggi")
        else:
            parts.append(f"data superata di {abs(days)} giorni")

    if goal.priority_notes:
        parts.append(f"nota: {goal.priority_notes}")

    return " · ".join(parts)
