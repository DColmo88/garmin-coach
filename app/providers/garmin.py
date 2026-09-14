"""Garmin come fornitore.

Un adattatore sottile: la sincronizzazione vera vive già in `app/garmin/sync.py`
e non è stata toccata. Qui c'è solo il pezzo che mancava — collegare e
scollegare un account — e la firma comune agli altri fornitori.
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.auth.security import encrypt_secret
from app.db.models import ProviderConnection, User

logger = logging.getLogger(__name__)


class GarminAuthError(Exception):
    """Credenziali rifiutate da Garmin."""


def connect(db: Session, user: User, email: str, password: str) -> ProviderConnection:
    """Verifica le credenziali con Garmin e le salva cifrate.

    La verifica è un login vero: è l'unico modo di sapere se le credenziali
    valgono, e vale la pena spenderlo qui invece di scoprire che sono sbagliate
    alle 6:30 del mattino dopo.
    """
    from app.garmin.client import validate_credentials

    email = email.strip().lower()
    if not validate_credentials(email, password):
        raise GarminAuthError(
            "Garmin ha rifiutato queste credenziali. Controlla email e password — "
            "e ricorda che l'account non deve avere la verifica in due passaggi attiva."
        )

    conn = user.connection or ProviderConnection(user_id=user.id)
    conn.provider = "garmin"
    conn.external_id = email
    conn.secret_encrypted = encrypt_secret(password)
    conn.access_token_encrypted = None
    conn.token_expires_at = None
    conn.status = "ok"

    if conn.id is None:
        db.add(conn)
    db.commit()
    db.refresh(user)
    return conn


def sync(db: Session, user: User, conn: ProviderConnection) -> dict[str, int]:
    """Tutte e sei le sincronizzazioni: attività, zone, benessere, sonno, training, corpo."""
    from app.garmin import sync as garmin_sync

    return garmin_sync.sync_all(db, user)
