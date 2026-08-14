"""Il dispatcher: da "c'è qualcosa da dire" a "l'utente lo ha ricevuto".

Valuta le regole, sceglie i canali che l'utente ha attivato, consegna e
registra l'esito. Il log serve sia alla deduplica sia a capire, dopo, perché
una notifica non è arrivata.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

from app import queries as q
from app.ai.readiness import ReadinessResult, compute_readiness
from app.db.models import NotificationLog, User
from app.goals import active_goal
from app.notifications import rules
from app.notifications.channels import CHANNELS, Channel
from app.notifications.rules import Notification

logger = logging.getLogger(__name__)


def channels_for(db: Session, user: User, event_type: str) -> list[Channel]:
    """I canali su cui recapitare questo evento a questo utente.

    Le preferenze hanno forma {"channels": {"email": true, ...}}. Se l'utente
    non ha mai toccato le impostazioni, si usano tutti i canali disponibili:
    meglio ricevere che perdersi un avviso di sovrallenamento.
    """
    prefs = (user.notify_prefs_json or {}).get("channels")
    out = []
    for key, channel in CHANNELS.items():
        if not channel.is_available_for(db, user):
            continue
        if prefs is not None and not prefs.get(key, False):
            continue
        out.append(channel)
    return out


def deliver(db: Session, user: User, notification: Notification) -> int:
    """Consegna su tutti i canali attivi. Restituisce quanti hanno funzionato."""
    channels = channels_for(db, user, notification.event_type)
    if not channels:
        logger.info(
            "Notifica '%s' per utente %s: nessun canale attivo.",
            notification.event_type, user.id,
        )
        return 0

    delivered = 0
    for channel in channels:
        try:
            ok = channel.send(db, user, notification)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Canale %s in errore", channel.key)
            ok = False

        db.add(NotificationLog(
            user_id=user.id,
            event_type=notification.event_type,
            channel=channel.key,
            title=notification.title,
            body=notification.body,
            ok=ok,
        ))
        if ok:
            delivered += 1

    db.commit()
    return delivered


def dispatch_for_user(
    db: Session,
    user: User,
    snap: dict | None = None,
    readiness: ReadinessResult | None = None,
    today: date | None = None,
) -> int:
    """Valuta le regole per un utente e consegna. Restituisce quante inviate.

    È il punto chiamato dalla pipeline dopo ogni sincronizzazione.
    """
    snap = snap if snap is not None else q.coach_snapshot(db, user.id)
    readiness = readiness if readiness is not None else compute_readiness(snap)
    goal = active_goal(db, user.id)

    notifications = rules.evaluate(db, user, snap, readiness, goal)

    weekly = rules.weekly_summary(db, user, snap, today)
    if weekly is not None:
        notifications.append(weekly)

    sent = 0
    for notification in notifications:
        if deliver(db, user, notification) > 0:
            sent += 1
    return sent
