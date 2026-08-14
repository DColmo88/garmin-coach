"""I canali di consegna: Web Push, email, Telegram.

Interfaccia identica per tutti: `send(user, notification) -> bool`.
Un canale non configurato non è un errore, semplicemente non è disponibile
(`is_configured` è False e il dispatcher lo salta).

Nessun canale solleva eccezioni verso l'alto: una notifica non consegnata non
deve mai far fallire la sincronizzazione notturna.
"""
from __future__ import annotations

import json
import logging
import smtplib
from abc import ABC, abstractmethod
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import PushSubscription, User
from app.notifications.rules import Notification

logger = logging.getLogger(__name__)


class Channel(ABC):
    """Un modo di recapitare una notifica."""

    key: str
    label: str

    @property
    @abstractmethod
    def is_configured(self) -> bool:
        """True se il canale ha tutto il necessario per funzionare."""
        raise NotImplementedError

    @abstractmethod
    def is_available_for(self, db: Session, user: User) -> bool:
        """True se questo utente ha collegato il canale."""
        raise NotImplementedError

    @abstractmethod
    def send(self, db: Session, user: User, notification: Notification) -> bool:
        """Consegna. False se non è riuscita, senza sollevare."""
        raise NotImplementedError


# ============================================================================
# Web Push (PWA)
# ============================================================================


class WebPushChannel(Channel):
    """Notifiche sul telefono senza app nativa, via service worker."""

    key = "webpush"
    label = "Notifiche sul telefono"

    @property
    def is_configured(self) -> bool:
        return bool(settings.VAPID_PUBLIC_KEY and settings.VAPID_PRIVATE_KEY)

    def is_available_for(self, db: Session, user: User) -> bool:
        if not self.is_configured:
            return False
        return db.scalar(
            select(PushSubscription.id).where(PushSubscription.user_id == user.id).limit(1)
        ) is not None

    def send(self, db: Session, user: User, notification: Notification) -> bool:
        if not self.is_configured:
            return False

        from pywebpush import WebPushException, webpush

        subscriptions = list(
            db.scalars(
                select(PushSubscription).where(PushSubscription.user_id == user.id)
            ).all()
        )
        if not subscriptions:
            return False

        payload = json.dumps({
            "title": notification.title,
            "body": notification.body,
            "url": notification.url,
        })
        delivered = False

        for subscription in subscriptions:
            try:
                webpush(
                    subscription_info={
                        "endpoint": subscription.endpoint,
                        "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
                    },
                    data=payload,
                    vapid_private_key=settings.VAPID_PRIVATE_KEY,
                    vapid_claims={"sub": f"mailto:{settings.VAPID_CONTACT_EMAIL}"},
                )
                delivered = True
            except WebPushException as exc:
                # 404/410 = il browser ha revocato l'iscrizione: va rimossa.
                status = getattr(exc.response, "status_code", None)
                if status in (404, 410):
                    logger.info("Iscrizione push scaduta per utente %s, la rimuovo.", user.id)
                    db.delete(subscription)
                    db.commit()
                else:
                    logger.warning("Push fallita per utente %s: %s", user.id, exc)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Push fallita per utente %s: %s", user.id, exc)

        return delivered


# ============================================================================
# Email
# ============================================================================


class EmailChannel(Channel):
    """Email via SMTP. Funziona con Brevo, Gmail o qualsiasi altro provider."""

    key = "email"
    label = "Email"

    @property
    def is_configured(self) -> bool:
        return bool(settings.SMTP_HOST and settings.SMTP_FROM)

    def is_available_for(self, db: Session, user: User) -> bool:
        return self.is_configured and bool(user.garmin_email)

    def send(self, db: Session, user: User, notification: Notification) -> bool:
        if not self.is_configured:
            return False

        message = EmailMessage()
        message["Subject"] = f"Garmin Coach · {notification.title}"
        message["From"] = settings.SMTP_FROM
        message["To"] = user.garmin_email
        link = f"{settings.PUBLIC_BASE_URL}{notification.url}"
        message.set_content(
            f"{notification.title}\n\n{notification.body}\n\n"
            f"Apri l'app: {link}\n\n"
            "—\nPer non ricevere più questo tipo di avvisi, cambia le preferenze "
            f"in {settings.PUBLIC_BASE_URL}/settings"
        )

        try:
            if settings.SMTP_USE_SSL:
                server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
            else:
                server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
                if settings.SMTP_USE_TLS:
                    server.starttls()
            with server:
                if settings.SMTP_USER:
                    server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                server.send_message(message)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Email fallita per utente %s: %s", user.id, exc)
            return False


# ============================================================================
# Telegram
# ============================================================================


class TelegramChannel(Channel):
    """Bot Telegram: gratis e arriva davvero sul telefono."""

    key = "telegram"
    label = "Telegram"

    @property
    def is_configured(self) -> bool:
        return bool(settings.TELEGRAM_BOT_TOKEN)

    def is_available_for(self, db: Session, user: User) -> bool:
        return self.is_configured and bool(user.telegram_chat_id)

    def send(self, db: Session, user: User, notification: Notification) -> bool:
        if not self.is_available_for(db, user):
            return False

        import httpx

        link = f"{settings.PUBLIC_BASE_URL}{notification.url}"
        text = f"*{notification.title}*\n\n{notification.body}\n\n[Apri l'app]({link})"
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": user.telegram_chat_id,
                    "text": text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            if response.status_code != 200:
                logger.warning("Telegram ha risposto %s: %s",
                               response.status_code, response.text[:200])
                return False
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Telegram fallito per utente %s: %s", user.id, exc)
            return False


# ============================================================================
# Registro
# ============================================================================

CHANNELS: dict[str, Channel] = {
    channel.key: channel
    for channel in (WebPushChannel(), EmailChannel(), TelegramChannel())
}


def available_channels(db: Session, user: User) -> list[Channel]:
    """I canali che questo utente può effettivamente ricevere."""
    return [c for c in CHANNELS.values() if c.is_available_for(db, user)]
