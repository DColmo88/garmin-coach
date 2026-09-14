"""Il coach in chat: conversazioni, contesto e orchestrazione.

Il router si occupa solo del protocollo di streaming; tutta la logica sta qui.

Come si tiene basso il costo di una conversazione lunga:
- al modello vanno gli ultimi `HISTORY_LIMIT` messaggi, non tutta la cronologia;
- il system prompt contiene una sintesi già calcolata, non serie di dati;
- il resto il modello se lo prende con i tool, solo se serve.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import queries as q
from app.ai import usage
from app.ai.base import ChatEvent, Finished, TextChunk
from app.ai.prompts import chat_system_prompt
from app.ai.provider import get_provider
from app.ai.readiness import compute_readiness
from app.ai.tools import make_executor, tool_specs
from app.db.models import ChatConversation, ChatMessage, User
from app.goals import active_goal

logger = logging.getLogger(__name__)

HISTORY_LIMIT = 20  # messaggi passati inviati al modello
TITLE_MAX_CHARS = 60

SUGGESTIONS = [
    "Come sto andando rispetto al mese scorso?",
    "Cosa dovrei fare oggi per il mio obiettivo?",
    "Il mio sonno sta peggiorando?",
    "Sto rischiando di allenarmi troppo?",
    "Analizza il mio ultimo allenamento",
]


class ChatUnavailable(Exception):
    """Il provider configurato non sa gestire la chat."""


# --------------------------- conversazioni ---------------------------


def list_conversations(db: Session, user_id: int, limit: int = 30) -> list[ChatConversation]:
    return list(
        db.scalars(
            select(ChatConversation)
            .where(ChatConversation.user_id == user_id)
            .order_by(ChatConversation.updated_at.desc())
            .limit(limit)
        ).all()
    )


def get_conversation(db: Session, user_id: int, conversation_id: int) -> ChatConversation | None:
    """Una conversazione dell'utente. None se non esiste o è di un altro."""
    return db.scalar(
        select(ChatConversation).where(
            ChatConversation.id == conversation_id, ChatConversation.user_id == user_id
        )
    )


def create_conversation(db: Session, user_id: int) -> ChatConversation:
    conversation = ChatConversation(user_id=user_id)
    db.add(conversation)
    db.commit()
    return conversation


def delete_conversation(db: Session, user_id: int, conversation_id: int) -> bool:
    conversation = get_conversation(db, user_id, conversation_id)
    if conversation is None:
        return False
    db.query(ChatMessage).filter(ChatMessage.conversation_id == conversation.id).delete()
    db.delete(conversation)
    db.commit()
    return True


def messages_of(db: Session, conversation_id: int) -> list[ChatMessage]:
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.id)
        ).all()
    )


def _title_from(text: str) -> str:
    single_line = " ".join(text.split())
    if len(single_line) <= TITLE_MAX_CHARS:
        return single_line
    return single_line[: TITLE_MAX_CHARS - 1].rstrip() + "…"


# --------------------------- contesto ---------------------------


def build_system_prompt(db: Session, user: User) -> str:
    """Il quadro delle ultime due settimane, non solo lo stato di oggi.

    Costa qualche centinaio di token in più del vecchio snapshot puntuale. È il
    posto giusto dove spenderli: senza le serie, il modello reagisce a una
    notte storta come se fosse una crisi.
    """
    from app.ai import briefing as briefing_builder

    snap = q.coach_snapshot(db, user.id)
    readiness = compute_readiness(snap)
    goal = active_goal(db, user.id)
    name = (user.display_name or user.email.split("@")[0]).strip()
    from app import providers

    return chat_system_prompt(
        name,
        briefing_builder.build(db, user),
        readiness,
        goal,
        datetime.now().strftime("%d/%m/%Y"),
        has_recovery_data=providers.has(user, providers.Capability.SLEEP),
    )


def _history_for_model(db: Session, conversation_id: int) -> list[dict[str, str]]:
    rows = messages_of(db, conversation_id)[-HISTORY_LIMIT:]
    return [{"role": m.role, "content": m.content} for m in rows]


# --------------------------- invio ---------------------------


def send_message(
    db: Session, user: User, conversation: ChatConversation, text: str
) -> Iterator[ChatEvent]:
    """Salva il messaggio dell'utente e genera la risposta in streaming.

    Rilancia `usage.QuotaExceeded` se l'utente ha esaurito i messaggi del
    giorno, e `ChatUnavailable` se il provider non regge la chat.
    """
    usage.check_quota(db, user, "chat")

    provider = get_provider()
    if not provider.supports_chat:
        raise ChatUnavailable(
            "La chat richiede un provider AI configurato. "
            "Imposta AI_PROVIDER e la chiave corrispondente nel .env."
        )

    user_message = ChatMessage(conversation_id=conversation.id, role="user", content=text)
    db.add(user_message)
    if conversation.title == "Nuova conversazione":
        conversation.title = _title_from(text)
    conversation.updated_at = datetime.utcnow()
    db.commit()

    system = build_system_prompt(db, user)
    history = _history_for_model(db, conversation.id)
    execute = make_executor(db, user.id)

    collected: list[str] = []
    tools_used: list[str] = []

    for event in provider.chat(system, history, tool_specs(), execute):
        if isinstance(event, TextChunk):
            collected.append(event.text)
        elif isinstance(event, Finished):
            tools_used = event.tools_used
            usage.record(
                db, user.id, "chat",
                model=event.model,
                tokens_in=event.tokens_in,
                tokens_out=event.tokens_out,
            )
        yield event

    answer = "".join(collected).strip()
    if answer:
        db.add(ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=answer,
            tools_used_json=tools_used or None,
        ))
        conversation.updated_at = datetime.utcnow()
        db.commit()
