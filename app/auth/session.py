"""Sessioni utente: cookie firmato + dependency FastAPI.

Niente sessioni server-side: il cookie contiene l'id utente firmato con
SESSION_SECRET e una scadenza. Se il segreto cambia, tutte le sessioni decadono.

Il cookie porta anche l'**epoca di sessione** dell'utente (`User.session_epoch`).
Senza, un token firmato valeva trenta giorni qualunque cosa succedesse dopo: il
cambio password non lo toccava, e nemmeno la disattivazione dell'account da
`/admin` — chi era già entrato restava dentro. Confrontare l'epoca a ogni
richiesta costa niente, perché l'utente lo si stava già caricando dal database
per restituirlo.
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


def create_session_token(user: User) -> str:
    """Il contenuto del cookie: chi sei e a quale epoca appartiene la sessione."""
    return _serializer().dumps({"uid": user.id, "e": user.session_epoch or 0})


def read_session_token(token: str) -> tuple[int, int] | None:
    """`(id utente, epoca)` se il token è valido e non scaduto, altrimenti None.

    I token della versione precedente non hanno il campo `e` e vengono
    rifiutati: chi era collegato rifà l'accesso una volta sola. Accettarli come
    «epoca 0» avrebbe lasciato in giro per trenta giorni esattamente i cookie
    che questo cambiamento serve a poter revocare.
    """
    try:
        payload = _serializer().loads(token, max_age=MAX_AGE_SEC)
        return int(payload["uid"]), int(payload["e"])
    except (BadSignature, SignatureExpired, KeyError, TypeError, ValueError):
        return None


def set_session_cookie(response, user: User) -> None:
    """Scrive il cookie di sessione sulla risposta.

    `secure` segue l'ambiente: in produzione (indirizzo pubblico in https) il
    cookie non deve mai viaggiare in chiaro; in sviluppo, su
    `http://localhost`, un cookie `Secure` il browser non lo manderebbe affatto
    e l'accesso locale smetterebbe di funzionare.
    """
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user),
        max_age=MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        secure=settings.is_production,
    )


def invalidate_sessions(db: Session, user: User) -> None:
    """Fa decadere tutti i cookie già emessi per questo utente.

    Si chiama quando la password cambia e quando l'account viene disattivato:
    sono i due momenti in cui una sessione aperta altrove non deve sopravvivere.
    """
    user.session_epoch = (user.session_epoch or 0) + 1
    db.commit()


def optional_user(request: Request, db: Session = Depends(get_session)) -> User | None:
    """Utente della sessione, o None se anonimo (per pagine pubbliche)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    parsed = read_session_token(token)
    if parsed is None:
        return None

    uid, epoch = parsed
    user = db.get(User, uid)
    if user is None:
        return None
    # Un account disattivato non deve poter proseguire con un cookie già in
    # mano: prima il controllo stava soltanto nel login, quindi «Disattiva»
    # in `/admin` non disattivava nessuno che fosse già entrato.
    if not user.is_active:
        return None
    if (user.session_epoch or 0) != epoch:
        return None
    return user


def require_user(request: Request, db: Session = Depends(get_session)) -> User:
    """Utente della sessione; solleva LoginRequired se assente o non valido."""
    user = optional_user(request, db)
    if user is None:
        raise LoginRequired()
    return user
