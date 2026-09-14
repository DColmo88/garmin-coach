"""Interfaccia astratta per i provider AI.

Il resto dell'app non sa quale modello sta parlando: chiede un testo o una
conversazione con tool e riceve sempre gli stessi tipi. Cambiare provider è
una variabile nel .env.

Tre capacità, in ordine di costo crescente:

- `coach()`   — messaggio del giorno (modello leggero, 1 volta al giorno)
- `chat()`    — conversazione con tool sui dati (modello leggero, streaming)
- `generate_training_plan()` — piano di allenamento (modello pesante)
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator


@dataclass
class ToolSpec:
    """Un tool che il modello può chiamare, in forma neutra rispetto al provider."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema dell'input


# --------------------------- eventi di streaming ---------------------------

@dataclass
class TextChunk:
    """Un pezzo di risposta da mostrare subito all'utente."""

    text: str


@dataclass
class ToolStarted:
    """Il modello sta consultando i dati: serve a mostrare 'sto guardando…'."""

    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class Finished:
    """Fine della risposta, con il conto dei token per il logging."""

    tokens_in: int = 0
    tokens_out: int = 0
    model: str | None = None
    tools_used: list[str] = field(default_factory=list)


ChatEvent = TextChunk | ToolStarted | Finished

# Esegue un tool e restituisce il risultato già serializzato in testo.
ToolExecutor = Callable[[str, dict[str, Any]], str]


class AIProviderError(Exception):
    """Errore di comunicazione con il provider AI."""


class AIProvider(ABC):
    """Contratto comune a tutti i provider."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Nome leggibile del provider (mostrato nella UI)."""
        raise NotImplementedError

    @property
    def supports_chat(self) -> bool:
        """True se questo provider può reggere la chat con tool."""
        return False

    @abstractmethod
    def generate_training_plan(
        self, context: dict[str, Any], goal: str, schema: dict[str, Any] | None = None
    ) -> str | dict[str, Any]:
        """Genera un piano di allenamento.

        Senza `schema` restituisce markdown. Con uno schema JSON restituisce un
        dizionario che lo rispetta: è quello che serve per disegnare un
        calendario invece di un muro di testo.
        """
        raise NotImplementedError

    def analyse(
        self, system: str, user: str, schema: dict[str, Any], heavy: bool = False
    ) -> dict[str, Any] | None:
        """Una domanda, una risposta JSON che rispetta `schema`. `None` se non si può.

        Serve a tutto ciò che è «leggi questi dati e dimmi cosa ne pensi, in
        una forma che so disegnare»: gli insight del giorno, i verdetti delle
        pagine. Restituire `None` invece di sollevare è voluto — chi chiama ha
        sempre una versione deterministica da mostrare, e una pagina non deve
        rompersi perché il modello non ha risposto.

        Il provider deterministico non analizza niente: dice di no e basta.
        """
        return None

    def coach(self, snap: dict, readiness, goal=None, briefing: str | None = None):  # type: ignore[no-untyped-def]
        """Coaching del giorno. Default: template deterministici, nessuna rete.

        Un provider AI reale fa override per generare prosa con un LLM.
        `goal` è l'obiettivo attivo dell'utente (UserGoal) o None.
        `briefing` è il quadro delle ultime due settimane (`app.ai.briefing`):
        serve solo ai provider AI, quello deterministico usa `snap`.
        """
        from app.ai.coaching import build_coach_output

        return build_coach_output(snap, readiness, goal)

    def chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        tools: list[ToolSpec],
        execute_tool: ToolExecutor,
        max_rounds: int = 6,
    ) -> Iterator[ChatEvent]:
        """Conversazione con tool, in streaming.

        `messages` è una lista di {"role": "user"|"assistant", "content": str}.
        `execute_tool(nome, argomenti)` viene chiamata dal provider quando il
        modello chiede un dato, e deve restituire testo già pronto.
        `max_rounds` limita i giri di tool per non far esplodere i costi.
        """
        raise NotImplementedError("Questo provider non supporta la chat.")
