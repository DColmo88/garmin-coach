"""Pagina di amministrazione: utenti, inviti, consumo AI.

Serve a rispondere a tre domande: chi sta usando l'app, sta sincronizzando, e
quanto sto spendendo. Accessibile solo agli utenti con `is_admin`.
"""
from __future__ import annotations

import logging
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai import usage
from app.auth.session import require_user
from app.clock import today_for
from app.db.database import get_session
from app.db.models import (
    AIUsageLog,
    Activity,
    ChatConversation,
    InviteCode,
    NotificationLog,
    User,
)
from app.templating import templates

logger = logging.getLogger(__name__)

router = APIRouter()


class NotAdmin(Exception):
    """Un utente non amministratore ha provato ad aprire /admin."""


def invite_link(code: str) -> str:
    """Il link da mandare a chi si invita.

    Non il codice da copiare: `/register` legge `?invite=` e riempie il campo
    da solo. Un codice nudo costringe chi lo riceve a capire dove incollarlo —
    e a sbagliare, perché somiglia a una password.
    """
    from app.config import settings

    return f"{settings.PUBLIC_BASE_URL}/register?invite={code}"


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise NotAdmin()
    return user


@router.get("/admin", response_class=HTMLResponse)
def admin_page(
    request: Request,
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
    created: str = "",
    ok: str = "",
    error: str = "",
):
    today = today_for(admin)
    users = list(db.scalars(select(User).order_by(User.id)).all())

    # Una riga per utente con quello che serve per capire se sta funzionando.
    rows = []
    for user in users:
        rows.append({
            "user": user,
            "activities": db.scalar(
                select(func.count(Activity.id)).where(Activity.user_id == user.id)
            ) or 0,
            "conversations": db.scalar(
                select(func.count(ChatConversation.id))
                .where(ChatConversation.user_id == user.id)
            ) or 0,
            "usage_30d": usage.summary(db, user.id, since=today - timedelta(days=30)),
        })

    invites = list(db.scalars(
        select(InviteCode).order_by(InviteCode.created_at.desc()).limit(20)
    ).all())
    used_by = {
        u.id: u.email
        for u in db.scalars(select(User).where(
            User.id.in_([i.used_by_id for i in invites if i.used_by_id])
        )).all()
    } if invites else {}

    notifications = list(db.scalars(
        select(NotificationLog).order_by(NotificationLog.sent_at.desc()).limit(15)
    ).all())

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "active": "admin",
            "user": admin,
            "garmin_configured": admin.connection is not None,
            "rows": rows,
            "invites": invites,
            "used_by": used_by,
            "notifications": notifications,
            "total_30d": usage.summary(db, since=today - timedelta(days=30)),
            "total_all": usage.summary(db),
            "created_code": created,
            "created_link": invite_link(created) if created else "",
            "invite_link": invite_link,
            "ok": ok,
            "error": error,
            "now": datetime.utcnow(),
        },
    )


@router.post("/admin/invite")
def create_invite(
    expires_days: int = Form(30),
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    code = secrets.token_urlsafe(8)
    expires = datetime.utcnow() + timedelta(days=expires_days) if expires_days else None
    db.add(InviteCode(code=code, created_by_id=admin.id, expires_at=expires))
    db.commit()
    return RedirectResponse(f"/admin?created={code}", status_code=303)


@router.post("/admin/users/{user_id}/toggle")
def toggle_user(
    user_id: int,
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Attiva o disattiva un utente. Un admin non può disattivare se stesso."""
    target = db.get(User, user_id)
    if target is not None and target.id != admin.id:
        target.is_active = not target.is_active
        # Disattivare deve avere effetto *adesso*. Finora il controllo su
        # `is_active` stava solo nel login: chi era già entrato continuava a
        # usare l'app — e a consumare quota AI — fino alla scadenza naturale
        # del cookie, cioè per trenta giorni.
        if not target.is_active:
            target.session_epoch = (target.session_epoch or 0) + 1
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/users/{user_id}/password")
def reset_user_password(
    user_id: int,
    new_password: str = Form(...),
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Reimposta la password di un utente.

    È il recupero password dell'app. Non c'è un «ho dimenticato» via email
    perché non c'è SMTP configurato, e per una cerchia ristretta un
    amministratore che reimposta è più semplice di un servizio in più da
    tenere in piedi.
    """
    from app.auth import service

    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse("/admin", status_code=303)
    try:
        service.set_password(db, target, new_password)
    except service.WeakPassword as exc:
        return RedirectResponse(f"/admin?error={exc}", status_code=303)
    return RedirectResponse(f"/admin?ok=Password di {target.email} reimpostata.",
                            status_code=303)


@router.post("/admin/users/{user_id}/quota")
def set_quota(
    user_id: int,
    chat_daily: int = Form(...),
    plans_monthly: int = Form(...),
    db: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    target = db.get(User, user_id)
    if target is not None:
        target.ai_quota_chat_daily = max(0, chat_daily)
        target.ai_quota_plans_monthly = max(0, plans_monthly)
        db.commit()
    return RedirectResponse("/admin", status_code=303)
