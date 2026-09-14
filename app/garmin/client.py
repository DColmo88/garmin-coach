"""Client Garmin per-utente.

Ogni utente ha il proprio client autenticato e il proprio tokenstore su disco
(`data/garmin_tokens/{user_id}/`). Il client resta in memoria finché il
processo vive; il token su disco sopravvive ai riavvii, così non si rifà il
login a ogni deploy.

Nessun MFA: gli account devono avere la 2FA disattivata.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from pathlib import Path

from garminconnect import Garmin

from app.auth.security import decrypt_secret
from app.config import settings
from app.db.models import User

logger = logging.getLogger(__name__)


class GarminClientError(Exception):
    """Errore di login o di comunicazione con Garmin Connect."""


_clients: dict[int, Garmin] = {}
_locks: dict[int, threading.Lock] = defaultdict(threading.Lock)


def _tokenstore(user_id: int) -> str:
    path = Path(settings.GARMIN_TOKENSTORE) / str(user_id)
    path.mkdir(parents=True, exist_ok=True)
    return str(path)


def get_client(user: User) -> Garmin:
    """Client autenticato per l'utente: riusa il token salvato, altrimenti login."""
    if user.id in _clients:
        return _clients[user.id]

    with _locks[user.id]:
        if user.id in _clients:  # un altro thread può averlo creato nel frattempo
            return _clients[user.id]

        tokenstore = _tokenstore(user.id)

        try:
            client = Garmin()
            client.login(tokenstore)
            logger.info("Login Garmin riuscito col token salvato (utente %s).", user.id)
            _clients[user.id] = client
            return client
        except Exception as exc:  # token assente o scaduto
            logger.info("Token utente %s non riutilizzabile (%s), login completo.", user.id, exc)

        # Le credenziali non stanno più su `users`: dalla v3 l'account dell'app
        # è indipendente dal fornitore, e Garmin è una connessione fra le altre.
        conn = getattr(user, "connection", None)
        if conn is None or conn.provider != "garmin" or not conn.secret_encrypted:
            raise GarminClientError(
                f"L'utente {user.id} non ha un account Garmin collegato."
            )

        try:
            password = decrypt_secret(conn.secret_encrypted)
            client = Garmin(email=conn.external_id, password=password)
            client.login()
            client.garth.dump(tokenstore)
            logger.info("Login Garmin riuscito con credenziali (utente %s).", user.id)
            _clients[user.id] = client
            return client
        except Exception as exc:
            raise GarminClientError(
                f"Login Garmin fallito per {conn.external_id}: {exc}"
            ) from exc


def reset_client(user_id: int) -> None:
    """Forza un nuovo login alla prossima chiamata (es. dopo un errore di auth)."""
    _clients.pop(user_id, None)


def validate_credentials(email: str, password: str) -> bool:
    """Verifica le credenziali con un login reale. Usato in registrazione."""
    try:
        client = Garmin(email=email, password=password)
        client.login()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("Validazione credenziali fallita per %s: %s", email, exc)
        return False
