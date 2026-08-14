"""Coaching message + allenamento suggerito — template deterministici.

Nessuna chiamata di rete. Seam pronto per un futuro ClaudeProvider che
faccia override di AIProvider.coach().
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ai.readiness import ReadinessResult
from app.db.models import UserGoal
from app.goals import days_to_target


@dataclass
class WorkoutSuggestion:
    icon: str
    type: str
    duration: str
    hr_zone: str
    note: str


@dataclass
class CoachOutput:
    """Messaggio del giorno + allenamento suggerito.

    L'allenamento è sempre deterministico. Il messaggio può essere riscritto
    da un provider AI, che in quel caso valorizza `source="ai"` e i token
    consumati (servono al log dei costi).
    """

    message: str
    workout: WorkoutSuggestion
    source: str = "deterministic"
    tokens_in: int = 0
    tokens_out: int = 0
    model: str | None = None


_WORKOUTS = {
    "Pronto": WorkoutSuggestion("🏃", "Sessione chiave", "50–70 min",
                                "Zona 4 — intervalli o tempo",
                                "Sei in forma: oggi spingi sulla qualità."),
    "Buono": WorkoutSuggestion("🏃", "Corsa moderata", "40–55 min",
                               "Zona 2–3 — < 155 bpm",
                               "Buona base: ritmo controllato, senza strafare."),
    "Discreto": WorkoutSuggestion("🏃", "Corsa easy", "30–45 min",
                                  "Zona 2 — < 145 bpm",
                                  "Mantieni la conversazione: oggi costruiamo volume aerobico."),
    "Stanco": WorkoutSuggestion("🚶", "Recovery / camminata", "20–30 min",
                                "Zona 1 — molto leggero",
                                "Recupero attivo: muoviti senza alzare il carico."),
    "Riposo": WorkoutSuggestion("💤", "Riposo", "—", "Nessuno",
                                "Il corpo chiede recupero: oggi riposo completo."),
}

_NO_DATA = WorkoutSuggestion("—", "—", "—", "—",
                             "Sincronizza i dati per ricevere un suggerimento.")


def _goal_line(goal: UserGoal | None, readiness: ReadinessResult) -> str:
    """Una frase che lega la giornata all'obiettivo, quando ce n'è uno."""
    if goal is None:
        return ""

    days = days_to_target(goal)
    if days is None:
        return f"Obiettivo: {goal.title}."

    if days < 0:
        return f"La data di «{goal.title}» è passata: aggiorna l'obiettivo."
    if days == 0:
        return f"Oggi è il giorno di «{goal.title}». In bocca al lupo."
    if days <= 7:
        return (f"«{goal.title}» tra {days} giorni: settimana di scarico, "
                "niente carichi nuovi.")
    if days <= 21 and readiness.label in ("Stanco", "Riposo"):
        return (f"Mancano {days} giorni a «{goal.title}» e la prontezza è bassa: "
                "meglio recuperare adesso che arrivare cotto.")
    return f"«{goal.title}» tra {days} giorni."


def _message(snap: dict, r: ReadinessResult, goal: UserGoal | None = None) -> str:
    if r.score is None:
        return ("Non ho ancora abbastanza dati. Premi Sincronizza per scaricare "
                "le tue metriche Garmin e ricevere il coaching di oggi.")
    parts: list[str] = []
    ss = snap.get("sleep_score")
    if ss is not None:
        parts.append(f"Hai dormito con uno score di {round(ss)}")
    hrv = snap.get("hrv_status")
    if hrv:
        parts.append(f"HRV {hrv.lower()}")
    bb = snap.get("body_battery_high")
    if bb is not None:
        parts.append(f"Body Battery a {round(bb)}")
    detail = ", ".join(parts) if parts else "Ecco il quadro di oggi"

    message = (f"Prontezza {r.score}/100 — {r.emoji} {r.label}. "
               f"{detail}. {r.recommendation}.")
    goal_line = _goal_line(goal, r)
    return f"{message} {goal_line}".strip()


def build_coach_output(
    snap: dict, readiness: ReadinessResult, goal: UserGoal | None = None
) -> CoachOutput:
    workout = _NO_DATA if readiness.score is None else _WORKOUTS[readiness.label]
    return CoachOutput(message=_message(snap, readiness, goal), workout=workout)
