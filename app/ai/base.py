"""Interfaccia astratta per il provider AI.

Provider-agnostica: oggi c'è solo uno stub; domani si aggiunge
un'implementazione OpenAI (o altro) senza toccare il resto dell'app.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class AIProvider(ABC):
    """Contratto comune per generare un piano di allenamento dai dati Garmin."""

    @abstractmethod
    def generate_training_plan(self, context: dict[str, Any], goal: str) -> str:
        """Genera un piano di allenamento.

        Args:
            context: dati strutturati dell'utente (attività, sonno, training).
            goal: obiettivo richiesto (es. "10k sub 50'", "recupero", ...).

        Returns:
            Il piano come testo (markdown).
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """Nome leggibile del provider."""
        raise NotImplementedError

    def coach(self, snap: dict, readiness, goal=None):  # type: ignore[no-untyped-def]
        """Coaching del giorno. Default: template deterministici (no rete).

        Un provider AI reale può fare override per generare prosa con un LLM.
        `goal` è l'obiettivo attivo dell'utente (UserGoal) o None.
        """
        from app.ai.coaching import build_coach_output

        return build_coach_output(snap, readiness, goal)
