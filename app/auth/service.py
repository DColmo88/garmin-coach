"""Login e registrazione.

Modello scelto: **le credenziali Garmin sono il login dell'app**. Nessuna
password separata da ricordare. Conseguenze gestite qui:

- il login normale non contatta Garmin (verifica bcrypt locale, istantanea);
- la registrazione sì, perché è l'unico modo di sapere se le credenziali sono
  vere — e richiede un codice invito;
- se l'utente cambia password su Garmin, il login locale fallisce ma quello
  remoto riesce: aggiorniamo hash e copia cifrata (self-healing).
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import encrypt_secret, hash_password, verify_password
from app.db.models import InviteCode, User


class InvalidCredentials(Exception):
    """Credenziali rifiutate sia localmente sia da Garmin."""


class InviteRequired(Exception):
    """Email sconosciuta e nessun codice invito fornito."""


class InvalidInvite(Exception):
    """Codice invito inesistente, già usato o scaduto."""


class AccountDisabled(Exception):
    """Utente disattivato dall'amministratore."""


def _default_validator(email: str, password: str) -> bool:
    # Import locale: evita un ciclo con app.garmin.client, che importa i modelli.
    from app.garmin.client import validate_credentials

    return validate_credentials(email, password)


def authenticate(
    db: Session,
    email: str,
    password: str,
    invite_code: str | None = None,
    garmin_validator: Callable[[str, str], bool] | None = None,
) -> User:
    """Autentica un utente esistente o ne registra uno nuovo con invito.

    `garmin_validator` si risolve qui e non come valore di default, altrimenti
    resterebbe legato alla funzione al momento dell'import e i test non
    potrebbero sostituirlo.
    """
    if garmin_validator is None:
        garmin_validator = _default_validator
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.garmin_email == email))

    if user is not None:
        if not user.is_active:
            raise AccountDisabled()
        if verify_password(password, user.garmin_password_hash):
            return user
        # Forse la password è cambiata su Garmin: chiediamo a loro.
        if garmin_validator(email, password):
            user.garmin_password_hash = hash_password(password)
            user.garmin_password_encrypted = encrypt_secret(password)
            db.commit()
            return user
        raise InvalidCredentials()

    # --- registrazione ---
    if not invite_code:
        raise InviteRequired()

    invite = db.scalar(select(InviteCode).where(InviteCode.code == invite_code))
    now = datetime.utcnow()
    if (
        invite is None
        or invite.used_by_id is not None
        or (invite.expires_at is not None and invite.expires_at < now)
    ):
        raise InvalidInvite()

    if not garmin_validator(email, password):
        raise InvalidCredentials()

    is_first = db.scalar(select(User.id).limit(1)) is None
    user = User(
        garmin_email=email,
        garmin_password_hash=hash_password(password),
        garmin_password_encrypted=encrypt_secret(password),
        display_name=email.split("@")[0].replace(".", " ").title(),
        is_admin=is_first,
    )
    db.add(user)
    db.flush()
    invite.used_by_id = user.id
    invite.used_at = now
    db.commit()
    return user
