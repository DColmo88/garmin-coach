"""Accesso: login con l'account dell'app, registrazione a invito, logout.

Collegare Garmin o Strava **non** si fa qui: è il passo successivo, e vive in
`app/routers/connect.py`. Tenerli separati è il punto di tutta questa modifica
— l'identità è una cosa, la sorgente dei dati un'altra.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.session import SESSION_COOKIE, optional_user, set_session_cookie
from app.db.database import get_session
from app.db.models import User
from app.sports import PRIMARY_SPORTS, SPORTS
from app.templating import templates

router = APIRouter()


def _destination(user: User) -> str:
    """Dopo l'accesso: al coach se c'è una sorgente, altrimenti a collegarla.

    È la traduzione in una riga della regola di prodotto: collegare i dati è la
    prima cosa da fare dopo essersi registrati, non una voce di menu da
    scoprire per conto proprio.
    """
    return "/coach" if user.connection is not None else "/connect"


def _authenticated(user: User) -> RedirectResponse:
    response = RedirectResponse(_destination(user), status_code=303)
    set_session_cookie(response, user)
    return response


# ============================================================================
# Login
# ============================================================================

def _login_page(request: Request, *, error: str | None = None,
                email: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": error, "email": email},
        status_code=200,
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: User | None = Depends(optional_user)):
    if user is not None:
        return RedirectResponse(_destination(user), status_code=303)
    return _login_page(request)


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_session),
):
    try:
        user = service.login(db, email, password)
    except service.InvalidCredentials:
        return _login_page(request, email=email, error="Email o password non corretti.")
    except service.AccountDisabled:
        return _login_page(request, email=email, error="Questo account è stato disattivato.")
    return _authenticated(user)


# ============================================================================
# Registrazione
# ============================================================================

def _register_page(request: Request, *, error: str | None = None, email: str = "",
                   display_name: str = "", invite_code: str = "",
                   primary_sport: str = "") -> HTMLResponse:
    return templates.TemplateResponse(
        "register.html",
        {
            "request": request, "error": error, "email": email,
            "display_name": display_name, "invite_code": invite_code,
            "primary_sport": primary_sport,
            "sports": SPORTS, "primary_sports": PRIMARY_SPORTS,
            "min_password": service.MIN_PASSWORD_LENGTH,
        },
        status_code=200,
    )


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, invite: str = "",
                  user: User | None = Depends(optional_user)):
    if user is not None:
        return RedirectResponse(_destination(user), status_code=303)
    # L'invito può arrivare nel link, così chi lo riceve non deve copiarlo.
    return _register_page(request, invite_code=invite)


@router.post("/register")
def register_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    password_confirm: str = Form(""),
    display_name: str = Form(""),
    invite_code: str = Form(""),
    primary_sport: str = Form(""),
    db: Session = Depends(get_session),
):
    page = lambda error: _register_page(  # noqa: E731 — sei rami, stesso ritorno
        request, error=error, email=email, display_name=display_name,
        invite_code=invite_code, primary_sport=primary_sport,
    )

    if password != password_confirm:
        return page("Le due password non coincidono.")

    try:
        user = service.register(
            db, email, password,
            display_name=display_name,
            invite_code=invite_code.strip() or None,
            primary_sport=primary_sport.strip() or None,
        )
    except service.EmailTaken:
        return page("Esiste già un account con questa email. Prova ad accedere.")
    except service.WeakPassword as exc:
        return page(str(exc))
    except service.InviteRequired:
        return page("Serve un codice invito: l'accesso è a cerchia ristretta.")
    except service.InvalidInvite:
        return page("Codice invito non valido, già usato o scaduto.")

    return _authenticated(user)


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
