"""Pagina della chat e streaming della risposta.

Lo streaming usa Server-Sent Events: il testo compare mentre il modello
scrive, invece di apparire tutto insieme dopo dieci secondi di attesa.
"""
from __future__ import annotations

import json
import logging
from typing import Iterator

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.ai import chat as chat_service
from app.ai import usage
from app.ai.base import AIProviderError, Finished, TextChunk, ToolStarted
from app.ai.provider import get_provider
from app.auth.session import require_user
from app.db.database import SessionLocal, get_session
from app.db.models import User
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

# Etichette leggibili per il messaggio "sto consultando…"
TOOL_LABELS = {
    "get_wellness": "i tuoi dati di benessere",
    "get_sleep": "il tuo sonno",
    "get_activities": "i tuoi allenamenti",
    "get_activity_detail": "il dettaglio dell'allenamento",
    "get_performance": "le tue metriche di performance",
    "get_body": "la tua composizione corporea",
    "get_goal": "il tuo obiettivo",
    "compare_periods": "il confronto fra periodi",
}


def _sse(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.get("/chat", response_class=HTMLResponse)
def chat_page(
    request: Request,
    conversation: int | None = None,
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    conversations = chat_service.list_conversations(db, user.id)

    current = None
    if conversation is not None:
        current = chat_service.get_conversation(db, user.id, conversation)
    elif conversations:
        current = conversations[0]

    messages = chat_service.messages_of(db, current.id) if current else []
    provider = get_provider()

    return templates.TemplateResponse(
        "chat.html",
        {
            "request": request,
            "active": "chat",
            "user": user,
            "garmin_configured": True,
            "conversations": conversations,
            "current": current,
            "messages": messages,
            "suggestions": chat_service.SUGGESTIONS,
            "chat_available": provider.supports_chat,
            "provider_name": provider.name,
            "remaining": usage.remaining(db, user, "chat"),
            "quota": user.ai_quota_chat_daily,
        },
    )


@router.post("/chat/new")
def chat_new(db: Session = Depends(get_session), user: User = Depends(require_user)):
    conversation = chat_service.create_conversation(db, user.id)
    return RedirectResponse(f"/chat?conversation={conversation.id}", status_code=303)


@router.post("/chat/{conversation_id}/delete")
def chat_delete(
    conversation_id: int,
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    chat_service.delete_conversation(db, user.id, conversation_id)
    return RedirectResponse("/chat", status_code=303)


@router.post("/chat/send")
def chat_send(
    message: str = Form(...),
    conversation_id: int | None = Form(None),
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Risponde in streaming SSE.

    Usa una sessione DB propria: quella della richiesta viene chiusa quando
    l'handler ritorna, mentre il generatore continua a lavorare dopo.
    """
    text = (message or "").strip()
    if not text:
        return StreamingResponse(
            iter([_sse("error", {"message": "Il messaggio è vuoto."})]),
            media_type="text/event-stream",
        )

    user_id = user.id

    def stream() -> Iterator[str]:
        session = SessionLocal()
        try:
            current_user = session.get(User, user_id)
            conversation = (
                chat_service.get_conversation(session, user_id, conversation_id)
                if conversation_id
                else None
            )
            if conversation is None:
                conversation = chat_service.create_conversation(session, user_id)
            yield _sse("start", {"conversation_id": conversation.id})

            for event in chat_service.send_message(session, current_user, conversation, text):
                if isinstance(event, TextChunk):
                    yield _sse("chunk", {"text": event.text})
                elif isinstance(event, ToolStarted):
                    yield _sse("tool", {
                        "tool": event.tool,
                        "label": TOOL_LABELS.get(event.tool, "i tuoi dati"),
                    })
                elif isinstance(event, Finished):
                    yield _sse("done", {
                        "conversation_id": conversation.id,
                        "title": conversation.title,
                        "remaining": usage.remaining(session, current_user, "chat"),
                    })

        except usage.QuotaExceeded as exc:
            yield _sse("error", {
                "message": (
                    f"Hai esaurito i messaggi di oggi ({exc.used}/{exc.limit}). "
                    "La quota si azzera a mezzanotte."
                )
            })
        except chat_service.ChatUnavailable as exc:
            yield _sse("error", {"message": str(exc)})
        except AIProviderError as exc:
            logger.exception("Provider AI in errore durante la chat")
            yield _sse("error", {
                "message": f"Il modello non ha risposto: {exc}. Riprova tra poco."
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("Errore imprevisto nella chat")
            yield _sse("error", {"message": f"Errore imprevisto: {exc}"})
        finally:
            session.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
