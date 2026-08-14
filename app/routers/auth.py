"""Route di accesso: login con credenziali Garmin, registrazione a invito, logout."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.session import MAX_AGE_SEC, SESSION_COOKIE, create_session_token, optional_user
from app.db.database import get_session
from app.db.models import User
from app.templating import templates

router = APIRouter()


def _login_page(request: Request, *, error: str | None = None,
                email: str = "", need_invite: bool = False) -> HTMLResponse:
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": error, "email": email, "need_invite": need_invite},
        status_code=200,
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: User | None = Depends(optional_user)):
    if user is not None:
        return RedirectResponse("/coach", status_code=303)
    return _login_page(request)


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    invite_code: str = Form(""),
    db: Session = Depends(get_session),
):
    try:
        user = service.authenticate(
            db, email, password, invite_code=invite_code.strip() or None
        )
    except service.InviteRequired:
        return _login_page(
            request,
            email=email,
            need_invite=True,
            error="Primo accesso: inserisci il codice invito che hai ricevuto.",
        )
    except service.InvalidInvite:
        return _login_page(
            request,
            email=email,
            need_invite=True,
            error="Codice invito non valido, già usato o scaduto.",
        )
    except service.InvalidCredentials:
        return _login_page(
            request,
            email=email,
            need_invite=bool(invite_code.strip()),
            error="Credenziali Garmin non valide. Controlla email e password.",
        )
    except service.AccountDisabled:
        return _login_page(request, email=email, error="Questo account è stato disattivato.")

    response = RedirectResponse("/coach", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.id),
        max_age=MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
    )
    return response


@router.post("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response
