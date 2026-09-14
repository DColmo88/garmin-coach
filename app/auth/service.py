"""Registrazione, accesso, password.

Il cambiamento rispetto alla v2: **l'account è dell'app, non di Garmin.**

Prima le credenziali Garmin erano il login. Funzionava finché esisteva un
fornitore solo, e cadeva su due punti: chi usa Strava non ha un account Garmin
da inserire, e chi cambia orologio perderebbe l'accesso ai propri dati storici.
Adesso ci si registra con email e password dell'app, e la sorgente dei dati si
collega dopo (`app/providers/`).

Conseguenza pratica: **il login non tocca la rete.** Prima, quando la password
Garmin cambiava, si ricadeva su una verifica remota; adesso non serve più —
l'unica prova è l'hash bcrypt locale, e un Garmin irraggiungibile non impedisce
a nessuno di entrare.

La registrazione resta **a invito**. Il recupero della password non passa da
un'email — non c'è SMTP configurato e non lo si vuole aggiungere — ma
dall'amministratore, che la reimposta da `/admin` o dalla CLI.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import hash_password, verify_password
from app.db.models import InviteCode, User
from app.sports import PRIMARY_SPORTS

# Corta abbastanza da non essere una seccatura, lunga abbastanza da non essere
# «1234». L'app è a invito: la difesa vera è quella, non la complessità.
MIN_PASSWORD_LENGTH = 8


class InvalidCredentials(Exception):
    """Email sconosciuta o password sbagliata."""


class InviteRequired(Exception):
    """Registrazione tentata senza codice invito."""


class InvalidInvite(Exception):
    """Codice invito inesistente, già usato o scaduto."""


class AccountDisabled(Exception):
    """Utente disattivato dall'amministratore."""


class EmailTaken(Exception):
    """Esiste già un account con questa email."""


class WeakPassword(Exception):
    """Password troppo corta."""


class ConfirmationMismatch(Exception):
    """La conferma digitata non corrisponde all'email dell'account."""


def normalise_email(email: str) -> str:
    return (email or "").strip().lower()


def _check_password(password: str) -> None:
    if len(password or "") < MIN_PASSWORD_LENGTH:
        raise WeakPassword(
            f"La password deve avere almeno {MIN_PASSWORD_LENGTH} caratteri."
        )


def _default_name(email: str) -> str:
    return email.split("@")[0].replace(".", " ").replace("_", " ").title()


# ============================================================================
# Accesso
# ============================================================================

def login(db: Session, email: str, password: str) -> User:
    """Autentica con le credenziali dell'app. Non contatta nessun fornitore."""
    email = normalise_email(email)
    user = db.scalar(select(User).where(func.lower(User.email) == email))

    # Stessa eccezione per «email sconosciuta» e «password sbagliata»: dire
    # quale delle due è servirebbe solo a chi prova indirizzi a caso.
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentials()
    if not user.is_active:
        raise AccountDisabled()
    return user


# ============================================================================
# Registrazione
# ============================================================================

def register(
    db: Session,
    email: str,
    password: str,
    display_name: str = "",
    invite_code: str | None = None,
    primary_sport: str | None = None,
) -> User:
    """Crea un account. Serve un invito valido; il primo utente diventa admin."""
    email = normalise_email(email)

    if db.scalar(select(User.id).where(func.lower(User.email) == email)) is not None:
        raise EmailTaken()

    _check_password(password)

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

    is_first = db.scalar(select(User.id).limit(1)) is None
    user = User(
        email=email,
        password_hash=hash_password(password),
        display_name=(display_name or "").strip() or _default_name(email),
        is_admin=is_first,
        # Non filtra niente: decide solo cosa si vede per primo (`app.sports`).
        primary_sport=(primary_sport if primary_sport in PRIMARY_SPORTS else None),
    )
    db.add(user)
    db.flush()
    invite.used_by_id = user.id
    invite.used_at = now
    db.commit()
    return user


# ============================================================================
# Password
# ============================================================================

def change_password(db: Session, user: User, current: str, new: str) -> None:
    """Cambio password dall'utente: serve conoscere quella vecchia."""
    if not verify_password(current, user.password_hash):
        raise InvalidCredentials()
    set_password(db, user, new)


def set_password(db: Session, user: User, new: str) -> None:
    """Imposta la password senza chiedere la vecchia: solo per l'admin e la CLI.

    Alza anche l'epoca di sessione, così i cookie già emessi smettono di
    valere. È il punto del cambio password che prima mancava: reimpostarla
    per un account compromesso non cacciava fuori chi era già entrato, e il
    cookie restava buono per altri trenta giorni.

    Chi cambia la password da solo si riprende il cookie subito dopo (vedi
    `app/routers/settings.py`): a essere buttate fuori sono le altre sessioni.
    """
    _check_password(new)
    user.password_hash = hash_password(new)
    user.session_epoch = (user.session_epoch or 0) + 1
    db.commit()


# ============================================================================
# Cancellazione dell'account
# ============================================================================
#
# Qui dentro ci sono anni di sonno, frequenza cardiaca a riposo, HRV, peso e
# composizione corporea: dati sanitari, categoria particolare. Che l'unico modo
# di liberarsene fosse chiedere all'amministratore non stava in piedi — e il
# `delete` non sarebbe nemmeno riuscito, perché fino a questa versione nessuna
# chiave esterna aveva un `ON DELETE`.
#
# La cancellazione è esplicita, tabella per tabella, **e** le chiavi esterne
# hanno `CASCADE`. Sembra una cintura con le bretelle, e lo è di proposito: il
# cascade copre le tabelle che qualcuno aggiungerà dopo e dimenticherà di
# mettere in questa lista, l'elenco esplicito copre il caso in cui il motore
# non applichi il cascade (SQLite senza il pragma acceso). Su un'operazione
# irreversibile, un residuo silenzioso è il modo peggiore di sbagliare.


def delete_account(db: Session, user: User) -> None:
    """Cancella l'utente e tutto ciò che gli appartiene. Non si torna indietro.

    Cosa **non** viene cancellato, e perché: i codici invito restano (le loro
    FK sono `SET NULL`), perché sono la traccia di chi è stato invitato e da
    chi, e non contengono dati dell'utente.
    """
    import logging
    import shutil
    from pathlib import Path

    from sqlalchemy import delete as sql_delete

    from app.config import settings
    from app.db.models import (
        Activity,
        AIUsageLog,
        BodyComposition,
        ChatConversation,
        ChatMessage,
        DailyCoachCache,
        DailyWellness,
        GamificationState,
        NotificationLog,
        ProviderConnection,
        PushSubscription,
        SleepRecord,
        TrainingMetric,
        TrainingPlan,
        UserGoal,
    )

    logger = logging.getLogger(__name__)
    user_id = user.id

    # Prima il fornitore: revocare l'autorizzazione dopo aver cancellato la
    # riga sarebbe impossibile, e lasciare un'app collegata su Strava per un
    # account che non esiste più è scortese oltre che inutile.
    conn = user.connection
    if conn is not None and conn.provider == "strava":
        try:
            from app.providers.strava import disconnect as strava_disconnect

            strava_disconnect(conn)
        except Exception:  # noqa: BLE001 — una revoca fallita non blocca nulla
            logger.warning("Revoca Strava non riuscita per l'utente %s", user_id)

    # I messaggi non hanno `user_id`: si raggiungono dalle conversazioni.
    conversation_ids = select(ChatConversation.id).where(
        ChatConversation.user_id == user_id
    )
    db.execute(
        sql_delete(ChatMessage).where(ChatMessage.conversation_id.in_(conversation_ids))
    )

    for model in (
        Activity, SleepRecord, TrainingMetric, DailyWellness, BodyComposition,
        UserGoal, TrainingPlan, DailyCoachCache, GamificationState,
        ChatConversation, AIUsageLog, NotificationLog, PushSubscription,
        ProviderConnection,
    ):
        db.execute(sql_delete(model).where(model.user_id == user_id))

    # `User.connection` è caricata con `selectin`, quindi l'ORM ha ancora in
    # mano la riga che la cancellazione di massa ha appena tolto e proverebbe a
    # cancellarla una seconda volta per via del `cascade` della relazione.
    # Scordarsela la fa rileggere, e trova che non c'è più.
    db.expire(user, ["connection"])

    db.delete(user)
    db.commit()

    # I token Garmin vivono su disco, fuori dal database: senza questo, la
    # cartella di un utente cancellato resterebbe lì con dentro le credenziali
    # di sessione del suo account Garmin.
    tokenstore = Path(settings.GARMIN_TOKENSTORE) / str(user_id)
    try:
        shutil.rmtree(tokenstore)
    except FileNotFoundError:
        pass
    except OSError:
        logger.warning("Token store non rimosso per l'utente %s", user_id)

    logger.info("Account %s cancellato su richiesta.", user_id)


def delete_own_account(db: Session, user: User, confirmation: str) -> None:
    """Cancellazione richiesta dall'utente: deve riscrivere la propria email.

    Non una spunta e non un «sei sicuro?»: digitare l'indirizzo è l'unica
    conferma che non si dà per sbaglio, ed è la convenzione che la gente ha già
    visto altrove per le cose che non si annullano.
    """
    if normalise_email(confirmation) != normalise_email(user.email):
        raise ConfirmationMismatch()
    delete_account(db, user)
