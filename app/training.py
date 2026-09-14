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
from sqlalchemy.orm import Session, object_session

from app.ai.base import AIProviderError
from app.ai.provider import get_provider
from app.clock import today_for
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


def _weeks_available(goal: UserGoal | None, today: date | None = None) -> int:
    """Quante settimane fino alla gara, entro limiti ragionevoli."""
    days = days_to_target(goal, today) if goal else None
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
    today = today_for(user)
    weeks = _weeks_available(goal, today)

    # Lo stesso briefing che riceve il coach in chat. Costa qualche centinaio di
    # token su un'operazione che si fa poche volte al mese, e toglie l'assurdità
    # di chiedere ritmi «calibrati sulle soglie dell'atleta» a un modello che le
    # soglie non le ha mai viste.
    from app.ai import briefing as briefing_builder
    from app.goals import describe_goal
    from app.sports import primary_sport

    previous = get_active_plan(db, user.id)
    previous_adherence = ""
    if previous is not None:
        from app import queries as q

        previous_adherence = adherence(
            previous, q.recent_activities(db, user.id, 200), today
        ).as_line()

    context = {
        "briefing": briefing_builder.build(db, user, today),
        "sport": primary_sport(user),
        "adherence": previous_adherence,
    }

    brief = (
        f"{describe_goal(goal, today)}. "
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
        start_date=_next_monday(today_for(user)),
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


def _next_monday(today: date) -> date:
    return today + timedelta(days=(7 - today.weekday()) % 7 or 7)


# --------------------------- lettura del piano ---------------------------


def _plan_today(plan: TrainingPlan) -> date:
    """Che giorno è per il proprietario del piano.

    Ricaduta per i chiamanti che non hanno l'utente sottomano; chi ce l'ha
    passa `today` e si risparmia la query.
    """
    session = object_session(plan)
    owner = session.get(User, plan.user_id) if session is not None else None
    return today_for(owner) if owner is not None else date.today()


def current_week_number(plan: TrainingPlan, today: date | None = None) -> int:
    """In che settimana del piano siamo. 1-based, limitata alla durata.

    `today` va passato, ed è quello dell'atleta: senza, il confine fra una
    settimana del piano e la successiva cadrebbe quando è mezzanotte sul
    server, che per chi sta a Roma vuol dire le due di notte.
    """
    today = today or _plan_today(plan)
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


# ============================================================================
# Aderenza: cosa era previsto, cosa è stato fatto
# ============================================================================
#
# Era il pezzo che mancava, e si sentiva: il piano veniva generato una volta e
# da lì in poi nessuno guardava più se l'atleta lo stesse seguendo. Il coach
# non sapeva che avevi saltato tre sedute, gli insight nemmeno, e
# `today_session` continuava a proporre la seduta del giovedì della settimana
# sei a chi era fermo da dieci giorni.
#
# È tutto deterministico e costa una query: le sedute previste stanno nel JSON
# del piano, quelle fatte nelle attività. Il confronto è per **giorno**, non
# per tipo: pretendere che un fartlek registrato come «corsa» corrisponda alla
# voce «intervalli» del piano vorrebbe dire segnare come mancata una seduta
# fatta. Il giorno invece è un fatto.

# I giorni di riposo non contano come sedute previste: saltare un riposo non è
# una mancanza, e contarlo abbasserebbe l'aderenza di chi si allena di più.
REST_TYPES = {"riposo", "rest", "off"}

# Sotto questa aderenza il piano non descrive più quello che sta succedendo.
ADHERENCE_ADRIFT = 60


@dataclass
class PlannedDay:
    """Una seduta prevista, con la data in cui cadeva."""

    day: date
    week: int
    weekday: str
    type: str
    detail: str
    done: bool = False


@dataclass
class Adherence:
    """Quanto il piano e la realtà si assomigliano."""

    planned: list[PlannedDay]
    extra: int          # allenamenti fatti in giorni che il piano lasciava vuoti
    days_elapsed: int
    # L'ultimo giorno considerato: ieri, non oggi. Serve a `recent_missed`, che
    # deve guardare indietro **da adesso** e non dall'ultima seduta prevista:
    # per un piano finito tre settimane fa «di recente» è giustamente vuoto.
    until: date | None = None

    @property
    def total(self) -> int:
        return len(self.planned)

    @property
    def done(self) -> int:
        return sum(1 for d in self.planned if d.done)

    @property
    def missed(self) -> list[PlannedDay]:
        return [d for d in self.planned if not d.done]

    @property
    def pct(self) -> int | None:
        """Percentuale di sedute previste effettivamente svolte."""
        if not self.total:
            return None
        return round(100 * self.done / self.total)

    @property
    def is_adrift(self) -> bool:
        """Vero quando il piano ha smesso di descrivere la realtà."""
        pct = self.pct
        return pct is not None and self.total >= 4 and pct < ADHERENCE_ADRIFT

    @property
    def recent_missed(self) -> list[PlannedDay]:
        """Le sedute saltate negli ultimi quattordici giorni."""
        if not self.planned or self.until is None:
            return []
        limit = self.until - timedelta(days=13)
        return [d for d in self.missed if d.day >= limit]

    def as_line(self) -> str:
        """Una riga per il briefing. Vuota se non c'è niente da dire."""
        if not self.total:
            return ""
        parts = [f"{self.done} sedute su {self.total} previste ({self.pct}%)"]
        if self.extra:
            plurale = "allenamento" if self.extra == 1 else "allenamenti"
            parts.append(f"{self.extra} {plurale} in più, fuori programma")
        saltate = self.recent_missed
        if saltate:
            quali = ", ".join(
                f"{d.weekday} {d.day.strftime('%d/%m')} ({d.type})" for d in saltate[:4]
            )
            parts.append(f"saltate di recente: {quali}")
        return "; ".join(parts)


def planned_days(plan: TrainingPlan, until: date) -> list[PlannedDay]:
    """Le sedute che il piano prevedeva fino a `until`, riposi esclusi."""
    if plan.start_date is None:
        return []

    out: list[PlannedDay] = []
    for week in plan.plan_json.get("weeks", []):
        number = week.get("number")
        if not isinstance(number, int) or number < 1:
            continue
        for entry in week.get("days", []):
            weekday = (entry.get("weekday") or "").lower()
            if weekday not in WEEKDAYS:
                continue
            when = plan.start_date + timedelta(
                days=(number - 1) * 7 + WEEKDAYS.index(weekday)
            )
            if when > until:
                continue
            kind = (entry.get("type") or "").lower()
            if kind in REST_TYPES:
                continue
            out.append(PlannedDay(
                day=when, week=number, weekday=weekday,
                type=kind or "allenamento", detail=entry.get("detail") or "",
            ))
    return out


def adherence(plan: TrainingPlan, activities, today: date | None = None) -> Adherence:
    """Confronta il calendario del piano con quello che è stato registrato.

    `until` è **ieri** e non oggi: la seduta di oggi potrebbe ancora essere
    fatta nel pomeriggio, e contarla come saltata alle otto del mattino
    sarebbe un rimprovero a qualcuno che non ha fatto ancora niente di male.
    """
    if plan is None or plan.start_date is None:
        return Adherence([], 0, 0)

    today = today or _plan_today(plan)
    until = today - timedelta(days=1)

    done_days = {
        a.start_time.date() for a in activities
        if a.start_time is not None and plan.start_date <= a.start_time.date() <= until
    }

    scheduled = planned_days(plan, until)
    for entry in scheduled:
        entry.done = entry.day in done_days

    planned_dates = {e.day for e in scheduled}
    extra = len(done_days - planned_dates)

    return Adherence(
        scheduled, extra, max(0, (until - plan.start_date).days + 1), until
    )


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
    today = today or _plan_today(plan)
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
