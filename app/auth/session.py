"""Sessioni utente: cookie firmato + dependency FastAPI.

Niente sessioni server-side: il cookie contiene l'id utente firmato con
SESSION_SECRET e una scadenza. Se il segreto cambia, tutte le sessioni decadono.
"""
from __future__ import annotations

from fastapi import Depends, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_session
from app.db.models import User

SESSION_COOKIE = "gc_session"
MAX_AGE_SEC = 30 * 24 * 3600


class LoginRequired(Exception):
    """Richiesta senza sessione valida: l'handler in main.py redirige a /login."""


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SESSION_SECRET, salt="gc-session")


def create_session_token(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session_token(token: str) -> int | None:
    """Id utente se il token è valido e non scaduto, altrimenti None."""
    try:
        return _serializer().loads(token, max_age=MAX_AGE_SEC)["uid"]
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return None


def optional_user(request: Request, db: Session = Depends(get_session)) -> User | None:
    """Utente della sessione, o None se anonimo (per pagine pubbliche)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    uid = read_session_token(token)
    return db.get(User, uid) if uid is not None else None


def require_user(request: Request, db: Session = Depends(get_session)) -> User:
    """Utente della sessione; solleva LoginRequired se assente o non valido."""
    user = optional_user(request, db)
    if user is None:
        raise LoginRequired()
    return user
