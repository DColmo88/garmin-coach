"""Provider AI concreti + factory.

- `StubProvider`  — nessuna rete: restituisce il payload strutturato. Default.
- `ClaudeProvider` — Anthropic. Modello leggero per chat e coaching, pesante per i piani.
- `OpenAIProvider` — equivalente su OpenAI.

Scelta da `AI_PROVIDER` nel .env. Chi non è configurato non viene mai istanziato,
così l'app parte anche senza chiavi API.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from app.ai.base import (
    AIProvider,
    AIProviderError,
    ChatEvent,
    Finished,
    TextChunk,
    ToolExecutor,
    ToolSpec,
    ToolStarted,
)
from app.config import settings

logger = logging.getLogger(__name__)

# Budget di output: la chat è conversazionale, il piano è un documento.
_CHAT_MAX_TOKENS = 1500
_COACH_MAX_TOKENS = 500
_PLAN_MAX_TOKENS = 8000


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
            "Imposta `AI_PROVIDER=claude` e `ANTHROPIC_API_KEY` nel `.env` per attivare "
            "la generazione automatica.\n\n"
            f"**Obiettivo:** {goal}\n\n"
            "```json\n" + pretty + "\n```\n"
        )


# ============================================================================
# Claude
# ============================================================================


class ClaudeProvider(AIProvider):
    """Anthropic. Haiku per chat e coaching, Sonnet per i piani."""

    def __init__(self) -> None:
        if not settings.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY mancante nel .env")
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self.light = settings.CLAUDE_MODEL_LIGHT
        self.heavy = settings.CLAUDE_MODEL_HEAVY

    @property
    def name(self) -> str:
        return f"claude:{self.light}"

    @property
    def supports_chat(self) -> bool:
        return True

    # --------------------------- helper ---------------------------

    @staticmethod
    def _to_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "input_schema": t.parameters}
            for t in tools
        ]

    @staticmethod
    def _text_of(response) -> str:  # type: ignore[no-untyped-def]
        return "".join(b.text for b in response.content if b.type == "text").strip()

    # --------------------------- coaching ---------------------------

    def coach(self, snap: dict, readiness, goal=None):  # type: ignore[no-untyped-def]
        """Riscrive il messaggio deterministico con un tono da coach.

        L'allenamento suggerito resta quello calcolato in Python: l'AI cambia
        le parole, non i numeri.
        """
        from app.ai.coaching import build_coach_output

        base = build_coach_output(snap, readiness, goal)
        if readiness.score is None:
            return base  # senza dati non c'è niente da raccontare

        from app.ai.prompts import coach_system_prompt, coach_user_prompt

        try:
            response = self.client.messages.create(
                model=self.light,
                max_tokens=_COACH_MAX_TOKENS,
                system=coach_system_prompt(),
                messages=[{"role": "user", "content": coach_user_prompt(snap, readiness, goal, base)}],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Coaching AI fallito, uso il messaggio deterministico: %s", exc)
            return base

        text = self._text_of(response)
        if text:
            base.message = text
            base.source = "ai"
            base.tokens_in = response.usage.input_tokens
            base.tokens_out = response.usage.output_tokens
            base.model = self.light
        return base

    # --------------------------- chat ---------------------------

    def chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        tools: list[ToolSpec],
        execute_tool: ToolExecutor,
        max_rounds: int = 6,
    ) -> Iterator[ChatEvent]:
        """Loop agentico: il modello chiede dati coi tool finché ha una risposta."""
        history: list[dict[str, Any]] = [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        tool_defs = self._to_tools(tools)
        tokens_in = tokens_out = 0
        tools_used: list[str] = []

        for _ in range(max_rounds):
            try:
                with self.client.messages.stream(
                    model=self.light,
                    max_tokens=_CHAT_MAX_TOKENS,
                    system=system,
                    tools=tool_defs,
                    messages=history,
                ) as stream:
                    for event in stream:
                        if (
                            event.type == "content_block_delta"
                            and event.delta.type == "text_delta"
                        ):
                            yield TextChunk(event.delta.text)
                    response = stream.get_final_message()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Chat Claude fallita")
                raise AIProviderError(str(exc)) from exc

            tokens_in += response.usage.input_tokens
            tokens_out += response.usage.output_tokens

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                yield Finished(tokens_in, tokens_out, self.light, tools_used)
                return

            history.append({"role": "assistant", "content": response.content})
            results = []
            for block in tool_uses:
                arguments = dict(block.input or {})
                tools_used.append(block.name)
                yield ToolStarted(block.name, arguments)
                try:
                    output = execute_tool(block.name, arguments)
                    is_error = False
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Tool %s fallito", block.name)
                    output = f"Errore nell'esecuzione: {exc}"
                    is_error = True
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": output,
                    "is_error": is_error,
                })
            history.append({"role": "user", "content": results})

        # Esaurito il numero di giri: meglio dirlo che restare muti.
        yield TextChunk(
            "\n\n_(Ho consultato parecchi dati senza arrivare a una conclusione. "
            "Prova a farmi una domanda più circoscritta.)_"
        )
        yield Finished(tokens_in, tokens_out, self.light, tools_used)

    # --------------------------- piani ---------------------------

    def generate_training_plan(self, context: dict[str, Any], goal: str) -> str:
        from app.ai.prompts import plan_system_prompt, plan_user_prompt

        try:
            response = self.client.messages.create(
                model=self.heavy,
                max_tokens=_PLAN_MAX_TOKENS,
                system=plan_system_prompt(),
                messages=[{"role": "user", "content": plan_user_prompt(context, goal)}],
            )
        except Exception as exc:  # noqa: BLE001
            raise AIProviderError(f"Generazione piano fallita: {exc}") from exc
        return self._text_of(response)


# ============================================================================
# OpenAI
# ============================================================================


class OpenAIProvider(AIProvider):
    """OpenAI. Stesso contratto, stessa divisione leggero/pesante."""

    def __init__(self) -> None:
        if not settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY mancante nel .env")
        from openai import OpenAI

        self.client = OpenAI(api_key=settings.OPENAI_API_KEY)
        self.model = settings.OPENAI_MODEL

    @property
    def name(self) -> str:
        return f"openai:{self.model}"

    @property
    def supports_chat(self) -> bool:
        return True

    @staticmethod
    def _to_tools(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    def coach(self, snap: dict, readiness, goal=None):  # type: ignore[no-untyped-def]
        from app.ai.coaching import build_coach_output

        base = build_coach_output(snap, readiness, goal)
        if readiness.score is None:
            return base

        from app.ai.prompts import coach_system_prompt, coach_user_prompt

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=_COACH_MAX_TOKENS,
                messages=[
                    {"role": "system", "content": coach_system_prompt()},
                    {"role": "user", "content": coach_user_prompt(snap, readiness, goal, base)},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Coaching AI fallito, uso il messaggio deterministico: %s", exc)
            return base

        text = (response.choices[0].message.content or "").strip()
        if text:
            base.message = text
            base.source = "ai"
            if response.usage:
                base.tokens_in = response.usage.prompt_tokens
                base.tokens_out = response.usage.completion_tokens
            base.model = self.model
        return base

    def chat(
        self,
        system: str,
        messages: list[dict[str, str]],
        tools: list[ToolSpec],
        execute_tool: ToolExecutor,
        max_rounds: int = 6,
    ) -> Iterator[ChatEvent]:
        history: list[dict[str, Any]] = [{"role": "system", "content": system}]
        history += [{"role": m["role"], "content": m["content"]} for m in messages]
        tool_defs = self._to_tools(tools)
        tokens_in = tokens_out = 0
        tools_used: list[str] = []

        for _ in range(max_rounds):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    max_tokens=_CHAT_MAX_TOKENS,
                    messages=history,
                    tools=tool_defs,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Chat OpenAI fallita")
                raise AIProviderError(str(exc)) from exc

            if response.usage:
                tokens_in += response.usage.prompt_tokens
                tokens_out += response.usage.completion_tokens

            message = response.choices[0].message
            if not message.tool_calls:
                if message.content:
                    yield TextChunk(message.content)
                yield Finished(tokens_in, tokens_out, self.model, tools_used)
                return

            history.append(message.model_dump(exclude_none=True))
            for call in message.tool_calls:
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                tools_used.append(call.function.name)
                yield ToolStarted(call.function.name, arguments)
                try:
                    output = execute_tool(call.function.name, arguments)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Tool %s fallito", call.function.name)
                    output = f"Errore nell'esecuzione: {exc}"
                history.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": output,
                })

        yield TextChunk(
            "\n\n_(Ho consultato parecchi dati senza arrivare a una conclusione. "
            "Prova a farmi una domanda più circoscritta.)_"
        )
        yield Finished(tokens_in, tokens_out, self.model, tools_used)

    def generate_training_plan(self, context: dict[str, Any], goal: str) -> str:
        from app.ai.prompts import plan_system_prompt, plan_user_prompt

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                max_tokens=_PLAN_MAX_TOKENS,
                messages=[
                    {"role": "system", "content": plan_system_prompt()},
                    {"role": "user", "content": plan_user_prompt(context, goal)},
                ],
            )
        except Exception as exc:  # noqa: BLE001
            raise AIProviderError(f"Generazione piano fallita: {exc}") from exc
        return (response.choices[0].message.content or "").strip()


# ============================================================================
# Factory
# ============================================================================


def get_provider() -> AIProvider:
    """Il provider scelto da AI_PROVIDER, con fallback sullo stub.

    Se il provider configurato non è utilizzabile (chiave mancante, SDK non
    installato) si torna allo stub invece di far crashare la pagina.
    """
    choice = (settings.AI_PROVIDER or "stub").lower()
    try:
        if choice == "claude":
            return ClaudeProvider()
        if choice == "openai":
            return OpenAIProvider()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Provider '%s' non utilizzabile (%s): uso lo stub.", choice, exc)
    return StubProvider()
