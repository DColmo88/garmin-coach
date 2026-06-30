"""Coaching message + allenamento suggerito — template deterministici.

Nessuna chiamata di rete. Seam pronto per un futuro ClaudeProvider che
faccia override di AIProvider.coach().
"""
from __future__ import annotations

from dataclasses import dataclass

from app.ai.readiness import ReadinessResult


@dataclass
class WorkoutSuggestion:
    icon: str
    type: str
    duration: str
    hr_zone: str
    note: str


@dataclass
class CoachOutput:
    message: str
    workout: WorkoutSuggestion


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


def _message(snap: dict, r: ReadinessResult) -> str:
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
    return (f"Prontezza {r.score}/100 — {r.emoji} {r.label}. "
            f"{detail}. {r.recommendation}.")


def build_coach_output(snap: dict, readiness: ReadinessResult) -> CoachOutput:
    workout = _NO_DATA if readiness.score is None else _WORKOUTS[readiness.label]
    return CoachOutput(message=_message(snap, readiness), workout=workout)
