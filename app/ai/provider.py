"""Provider AI concreti + factory.

Per ora è attivo solo `StubProvider`, che NON chiama nessuna AI: restituisce
il payload strutturato pronto da incollare in un modello (o da ispezionare).
`OpenAIProvider` è uno scheletro già pronto: quando vorrai, bastano la API key
nel .env e la chiamata vera all'SDK.
"""
from __future__ import annotations

import json
from typing import Any

from app.ai.base import AIProvider
from app.config import settings


class StubProvider(AIProvider):
    """Non chiama nessuna AI: prepara e restituisce il contesto strutturato."""

    @property
    def name(self) -> str:
        return "stub (nessuna AI collegata)"

    def generate_training_plan(self, context: dict[str, Any], goal: str) -> str:
        payload = {"goal": goal, "data": context}
        pretty = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
        return (
            "## Provider AI non ancora configurato\n\n"
            "Questo è il payload strutturato che verrà inviato al modello AI.\n"
            "Imposta `AI_PROVIDER=openai` e `OPENAI_API_KEY` nel `.env` per attivare "
            "la generazione automatica.\n\n"
            f"**Obiettivo:** {goal}\n\n"
            "```json\n" + pretty + "\n```\n"
        )


class OpenAIProvider(AIProvider):
    """Scheletro per l'integrazione OpenAI (da completare quando vorrai)."""

    def __init__(self) -> None:
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY mancante nel .env")
        self.model = settings.OPENAI_MODEL

    @property
    def name(self) -> str:
        return f"openai:{self.model}"

    def generate_training_plan(self, context: dict[str, Any], goal: str) -> str:
        # TODO: implementazione reale, es:
        #   from openai import OpenAI
        #   client = OpenAI(api_key=settings.OPENAI_API_KEY)
        #   resp = client.chat.completions.create(model=self.model, messages=[...])
        #   return resp.choices[0].message.content
        raise NotImplementedError(
            "OpenAIProvider non ancora implementato. Aggiungi l'SDK openai e la chiamata."
        )


def get_provider() -> AIProvider:
    """Factory: sceglie il provider in base a AI_PROVIDER nel .env."""
    if settings.AI_PROVIDER == "openai":
        return OpenAIProvider()
    return StubProvider()
