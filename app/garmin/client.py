"""Wrapper attorno alla libreria `garminconnect`.

Gestisce il login con cache del token su disco: la prima volta usa
email/password, le volte successive riusa il token salvato (niente
re-login a ogni avvio). Nessun MFA (account senza 2FA).
"""
from __future__ import annotations

import logging
from pathlib import Path

from garminconnect import Garmin

from app.config import settings

logger = logging.getLogger(__name__)


class GarminClientError(Exception):
    """Errore di login o di comunicazione con Garmin Connect."""


_client: Garmin | None = None


def get_client() -> Garmin:
    """Restituisce un client Garmin autenticato (singleton).

    Strategia:
    1. Prova a riusare il token salvato in GARMIN_TOKENSTORE.
    2. Se non c'è o è scaduto, fa login con email/password e salva il token.
    """
    global _client
    if _client is not None:
        return _client

    if not settings.garmin_configured:
        raise GarminClientError(
            "Credenziali Garmin mancanti: imposta GARMIN_EMAIL e GARMIN_PASSWORD nel file .env"
        )

    tokenstore = settings.GARMIN_TOKENSTORE
    Path(tokenstore).mkdir(parents=True, exist_ok=True)

    # 1) Prova con il token salvato
    try:
        client = Garmin()
        client.login(tokenstore)
        logger.info("Login Garmin riuscito riusando il token salvato.")
        _client = client
        return client
    except Exception as exc:  # token assente/scaduto -> login completo
        logger.info("Token non riutilizzabile (%s), eseguo login completo.", exc)

    # 2) Login completo con credenziali, poi salva il token
    try:
        client = Garmin(email=settings.GARMIN_EMAIL, password=settings.GARMIN_PASSWORD)
        client.login()
        client.garth.dump(tokenstore)
        logger.info("Login Garmin riuscito con credenziali, token salvato in %s", tokenstore)
        _client = client
        return client
    except Exception as exc:
        raise GarminClientError(f"Login Garmin fallito: {exc}") from exc


def reset_client() -> None:
    """Forza un nuovo login alla prossima chiamata (es. dopo errore di auth)."""
    global _client
    _client = None
