"""Pagina impostazioni: preferenze notifiche, canali, account."""
from __future__ import annotations

import logging
import secrets
from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import queries as q
from app import sports
from app.analysis import cache as analysis_cache
from app.auth import service as auth_service
from app.auth.session import require_user, set_session_cookie
from app.clock import today_for
from app.config import settings as config
from app.db.database import get_session
from app.db.models import NotificationLog, PushSubscription, User
from app.notifications import rules
from app.notifications.channels import CHANNELS
from app.notifications.dispatcher import deliver
from app.notifications.rules import Notification
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


def _channel_state(db: Session, user: User) -> list[dict]:
    """Stato di ogni canale: configurato dal server, collegato dall'utente, acceso."""
    prefs = (user.notify_prefs_json or {}).get("channels")
    out = []
    for key, channel in CHANNELS.items():
        out.append({
            "key": key,
            "label": channel.label,
            "configured": channel.is_configured,
            "linked": channel.is_available_for(db, user),
            "enabled": True if prefs is None else bool(prefs.get(key, False)),
        })
    return out


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
    saved: str = "",
    pw_ok: str = "",
    pw_error: str = "",
):
    event_prefs = (user.notify_prefs_json or {}).get("events") or rules.default_prefs()
    recent = list(db.scalars(
        select(NotificationLog)
        .where(NotificationLog.user_id == user.id)
        .order_by(NotificationLog.sent_at.desc())
        .limit(10)
    ).all())

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "active": "settings",
            "user": user,
            "garmin_configured": user.connection is not None,
            "connection": user.connection,
            "pw_ok": pw_ok,
            "pw_error": pw_error,
            "min_password": auth_service.MIN_PASSWORD_LENGTH,
            "event_types": rules.EVENT_TYPES,
            "event_prefs": event_prefs,
            "channels": _channel_state(db, user),
            # Il profilo risolto serve a mostrare, accanto a ogni campo vuoto,
            # quale valore l'app sta usando al suo posto e da dove viene.
            "profile": analysis_cache.profile(db, user),
            "current_year": today_for(user).year,
            "sports": sports.SPORTS,
            "primary_sports": sports.PRIMARY_SPORTS,
            "current_sport": sports.primary_sport(user),
            # Se lo storico dice il contrario di quello che l'utente ha scelto,
            # glielo si fa notare invece di lasciarlo con una vista sbagliata.
            "suggested_sport": sports.infer_primary_sport(
                q.recent_activities(db, user.id, 100)
            ),
            "vapid_public_key": config.VAPID_PUBLIC_KEY,
            "telegram_bot": config.TELEGRAM_BOT_TOKEN.split(":")[0] if config.TELEGRAM_BOT_TOKEN else "",
            "recent": recent,
            "saved": bool(saved),
        },
    )


# --------------------------- profilo fisiologico ---------------------------

# Intervalli plausibili: fuori da questi il valore è un errore di battitura, e
# un errore di battitura qui falsa ogni carico calcolato da qui in avanti.
PROFILE_RANGES = {
    "hr_max": (120, 230),
    "hr_rest": (25, 110),
    "lthr": (100, 220),
    "ftp": (50, 600),
    "birth_year": (1920, date.today().year),
}


def _clean_int(raw: str, field: str) -> int | None:
    """Un intero dentro il suo intervallo, oppure `None` (campo lasciato vuoto)."""
    value = (raw or "").strip()
    if not value:
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    low, high = PROFILE_RANGES[field]
    return number if low <= number <= high else None


@router.post("/settings/sport")
async def save_primary_sport(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Cambia lo sport principale. Un valore non previsto lascia le cose com'erano."""
    form = await request.form()
    choice = str(form.get("primary_sport") or "").strip().lower()
    if choice in sports.PRIMARY_SPORTS:
        user.primary_sport = choice
        db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/password")
def change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_confirm: str = Form(""),
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Cambio password. Serve conoscere quella attuale."""
    from app.auth import service

    if new_password != new_password_confirm:
        return RedirectResponse("/settings?pw_error=Le due password non coincidono.",
                                status_code=303)
    try:
        service.change_password(db, user, current_password, new_password)
    except service.InvalidCredentials:
        return RedirectResponse("/settings?pw_error=La password attuale non è corretta.",
                                status_code=303)
    except service.WeakPassword as exc:
        return RedirectResponse(f"/settings?pw_error={exc}", status_code=303)

    # Il cambio password invalida tutte le sessioni, compresa questa. Chi ha
    # appena digitato la password vecchia ha dimostrato di essere lui, quindi
    # gli si riconsegna un cookie nuovo: a restare fuori sono gli altri
    # dispositivi, che è il punto.
    response = RedirectResponse("/settings?pw_ok=1", status_code=303)
    set_session_cookie(response, user)
    return response


@router.post("/settings/delete-account")
def delete_account(
    confirm_email: str = Form(""),
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Cancella il proprio account e tutti i dati. Irreversibile.

    Serve riscrivere la propria email: qui dentro ci sono anni di sonno, HRV,
    frequenza a riposo e peso, ed è la categoria di dati per cui poterseli
    portare via da soli non è una cortesia.
    """
    from app.auth import service
    from app.auth.session import SESSION_COOKIE

    try:
        service.delete_own_account(db, user, confirm_email)
    except service.ConfirmationMismatch:
        return RedirectResponse(
            "/settings?pw_error=Per cancellare l'account riscrivi il tuo indirizzo email.",
            status_code=303,
        )

    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.post("/settings/profile")
async def save_profile(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Salva le soglie fisiologiche. Un campo vuoto torna alla stima automatica."""
    form = await request.form()

    for field in PROFILE_RANGES:
        setattr(user, field, _clean_int(str(form.get(field) or ""), field))

    sex = str(form.get("sex") or "").strip().lower()
    user.sex = sex if sex in {"m", "f"} else None

    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/notifications")
async def save_notifications(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Salva quali eventi ricevere e su quali canali."""
    form = await request.form()
    prefs = dict(user.notify_prefs_json or {})
    prefs["events"] = {e.key: f"event_{e.key}" in form for e in rules.EVENT_TYPES}
    prefs["channels"] = {key: f"channel_{key}" in form for key in CHANNELS}
    user.notify_prefs_json = prefs
    db.commit()
    return RedirectResponse("/settings?saved=1", status_code=303)


@router.post("/settings/test-notification")
def test_notification(
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Invia una notifica di prova, per verificare che i canali funzionino."""
    sent = deliver(db, user, Notification(
        "test",
        "Notifica di prova",
        "Se stai leggendo questo, il canale funziona.",
        url="/settings",
    ))
    return {"sent": sent}


# --------------------------- Web Push ---------------------------


@router.post("/settings/push/subscribe")
async def push_subscribe(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Registra l'iscrizione push inviata dal browser."""
    try:
        payload = await request.json()
        endpoint = payload["endpoint"]
        keys = payload["keys"]
        p256dh, auth = keys["p256dh"], keys["auth"]
    except (KeyError, TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "iscrizione non valida"})

    existing = db.scalar(
        select(PushSubscription).where(
            PushSubscription.user_id == user.id, PushSubscription.endpoint == endpoint
        )
    )
    if existing is None:
        db.add(PushSubscription(user_id=user.id, endpoint=endpoint,
                                p256dh=p256dh, auth=auth))
    else:
        existing.p256dh, existing.auth = p256dh, auth
    db.commit()
    return {"status": "ok"}


@router.post("/settings/push/unsubscribe")
async def push_unsubscribe(
    request: Request,
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    payload = await request.json()
    endpoint = payload.get("endpoint")
    if endpoint:
        db.query(PushSubscription).filter(
            PushSubscription.user_id == user.id,
            PushSubscription.endpoint == endpoint,
        ).delete()
        db.commit()
    return {"status": "ok"}


# --------------------------- Telegram ---------------------------


@router.post("/settings/telegram/link")
def telegram_link(
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Genera il codice da mandare al bot per collegare l'account."""
    prefs = dict(user.notify_prefs_json or {})
    code = prefs.get("telegram_link_code")
    if not code:
        code = secrets.token_urlsafe(6)
        prefs["telegram_link_code"] = code
        user.notify_prefs_json = prefs
        db.commit()
    return {"code": code}


@router.post("/settings/telegram/unlink")
def telegram_unlink(
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    user.telegram_chat_id = None
    db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/telegram/webhook/{secret}")
async def telegram_webhook(
    secret: str,
    request: Request,
    db: Session = Depends(get_session),
):
    """Riceve i messaggi del bot e collega l'account con `/start <codice>`.

    È l'unico endpoint pubblico dell'app: la protezione è il `secret` nell'URL,
    che solo Telegram conosce perché glielo abbiamo dato registrando il webhook.
    """
    if not config.TELEGRAM_BOT_TOKEN or secret != config.telegram_webhook_secret:
        return JSONResponse(status_code=404, content={"error": "not found"})

    try:
        update = await request.json()
        message = update.get("message") or {}
        chat_id = str(message["chat"]["id"])
        text = (message.get("text") or "").strip()
    except (KeyError, TypeError, ValueError):
        return {"ok": True}  # Telegram non deve ritentare per un payload strano

    if not text.startswith("/start"):
        return {"ok": True}

    parts = text.split(maxsplit=1)
    code = parts[1].strip() if len(parts) > 1 else ""
    if not code:
        _telegram_reply(chat_id, "Per collegarti, apri le impostazioni dell'app e "
                                 "premi «Collega Telegram».")
        return {"ok": True}

    user = _user_by_link_code(db, code)
    if user is None:
        _telegram_reply(chat_id, "Codice non valido o scaduto. Generane uno nuovo "
                                 "dalle impostazioni dell'app.")
        return {"ok": True}

    user.telegram_chat_id = chat_id
    prefs = dict(user.notify_prefs_json or {})
    prefs.pop("telegram_link_code", None)  # il codice è monouso
    user.notify_prefs_json = prefs
    db.commit()

    _telegram_reply(chat_id, f"Collegato. Ciao {user.display_name or 'atleta'}, "
                             "da ora ti scrivo qui quando c'è qualcosa da sapere.")
    return {"ok": True}


def _user_by_link_code(db: Session, code: str) -> User | None:
    """Trova l'utente che ha generato questo codice di collegamento."""
    for user in db.scalars(select(User).where(User.notify_prefs_json.isnot(None))).all():
        if (user.notify_prefs_json or {}).get("telegram_link_code") == code:
            return user
    return None


def _telegram_reply(chat_id: str, text: str) -> None:
    """Risponde nella chat del bot. Un fallimento non deve rompere il webhook."""
    import httpx

    try:
        httpx.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Risposta Telegram non inviata: %s", exc)
