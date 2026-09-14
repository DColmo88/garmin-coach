"""Collegare la sorgente dei dati: Garmin **oppure** Strava.

È il secondo passo dopo la registrazione, e l'unico posto dell'app in cui si
dichiara che i due fornitori non danno le stesse cose. Detta qui, la differenza
è una scelta informata; ripetuta a ogni pagina sarebbe una lamentela — per
questo `/sleep`, `/health` e `/body` per un utente Strava non esistono proprio
invece di spiegarsi.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app import providers
from app.auth.session import require_user
from app.config import settings
from app.db.database import get_session
from app.db.models import User
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()

# L'autorizzazione va e torna dal browser dell'utente passando da Strava: il
# `state` firmato è ciò che impedisce a qualcun altro di far arrivare un codice
# di autorizzazione suo sull'account di un altro.
STATE_MAX_AGE_SEC = 600


def _back(message: str, key: str = "error") -> RedirectResponse:
    """Torna a `/connect` con un messaggio, codificato per finire in un URL.

    I messaggi arrivano dalle eccezioni dei fornitori: sono testi nostri, ma
    contengono trattini, virgole e apostrofi, e infilarli grezzi in una query
    string significa che il primo `&` di un messaggio futuro taglia la frase a
    metà. Meglio deciderlo una volta qui che ricordarselo in cinque punti.
    """
    return RedirectResponse(f"/connect?{key}={quote(message)}", status_code=303)


def _state_serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SESSION_SECRET, salt="strava-oauth")


def _redirect_uri() -> str:
    return f"{settings.PUBLIC_BASE_URL}/connect/strava/callback"


# ============================================================================
# La schermata di scelta
# ============================================================================

@router.get("/connect", response_class=HTMLResponse)
def connect_page(
    request: Request,
    error: str = "",
    ok: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_session),
):
    return templates.TemplateResponse(
        "connect.html",
        {
            "request": request,
            "user": user,
            "error": error,
            "ok": ok,
            "current": user.connection,
            "current_provider": providers.provider_of(user),
            "garmin": providers.GARMIN_PROVIDER,
            "strava": providers.STRAVA_PROVIDER,
            "strava_available": settings.strava_configured,
            "capability": providers.Capability,
            "history": providers.sections_with_history(db, user),
        },
    )


# ============================================================================
# Garmin: email e password, verificate con un login vero
# ============================================================================

@router.post("/connect/garmin")
def connect_garmin(
    email: str = Form(...),
    password: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_session),
):
    from app.providers.garmin import GarminAuthError, connect

    try:
        connect(db, user, email, password)
    except GarminAuthError as exc:
        return _back(str(exc))

    return RedirectResponse("/coach?nuova_sorgente=1", status_code=303)


# ============================================================================
# Strava: OAuth
# ============================================================================

@router.get("/connect/strava")
def connect_strava(user: User = Depends(require_user)):
    from app.providers.strava import StravaError, authorization_url

    try:
        url = authorization_url(_redirect_uri(), _state_serializer().dumps({"uid": user.id}))
    except StravaError as exc:
        return _back(str(exc))
    return RedirectResponse(url, status_code=303)


@router.get("/connect/strava/callback")
def connect_strava_callback(
    code: str = "",
    state: str = "",
    error: str = "",
    user: User = Depends(require_user),
    db: Session = Depends(get_session),
):
    from app.providers.strava import StravaError, connect

    if error or not code:
        # Succede anche solo premendo «Annulla» su Strava: non è un guasto.
        return _back("Autorizzazione Strava annullata.")

    try:
        payload = _state_serializer().loads(state, max_age=STATE_MAX_AGE_SEC)
    except (BadSignature, SignatureExpired, TypeError):
        return _back("Richiesta scaduta o non valida. Riprova a collegare Strava.")

    # Il `state` è legato a un utente: un codice ottenuto in un'altra sessione
    # non può finire su questo account.
    if payload.get("uid") != user.id:
        return _back("Questa autorizzazione non appartiene al tuo account.")

    try:
        connect(db, user, code)
    except StravaError as exc:
        return _back(str(exc))

    return RedirectResponse("/coach?nuova_sorgente=1", status_code=303)


# ============================================================================
# Scollegare
# ============================================================================

@router.post("/connect/disconnect")
def disconnect(
    user: User = Depends(require_user),
    db: Session = Depends(get_session),
):
    """Toglie la connessione. **I dati già scaricati restano.**

    Sono dati dell'utente, non del fornitore: cancellarli perché si cambia
    orologio vorrebbe dire perdere anni di storico per un cambio di attrezzo.
    """
    conn = user.connection
    if conn is None:
        return RedirectResponse("/connect", status_code=303)

    if conn.provider == providers.STRAVA:
        from app.providers.strava import disconnect as strava_disconnect

        strava_disconnect(conn)

    db.delete(conn)
    db.commit()
    return _back("Sorgente scollegata. I dati già scaricati restano.", key="ok")
