"""CLI amministrativa.

    python -m app.cli create-invite [--expires-days 30]
    python -m app.cli list-users
    python -m app.cli set-admin <email>
    python -m app.cli bootstrap-from-env
"""
from __future__ import annotations

import argparse
import os
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select

from app.db.database import SessionLocal, init_db
from app.db.models import InviteCode, User


def create_invite(expires_days: int | None = None) -> str:
    """Crea un codice invito e lo restituisce."""
    init_db()
    code = secrets.token_urlsafe(8)
    expires = datetime.utcnow() + timedelta(days=expires_days) if expires_days else None
    db = SessionLocal()
    try:
        db.add(InviteCode(code=code, expires_at=expires))
        db.commit()
    finally:
        db.close()
    return code


def list_users() -> list[str]:
    """Righe descrittive degli utenti registrati."""
    init_db()
    db = SessionLocal()
    try:
        rows = []
        for u in db.scalars(select(User).order_by(User.id)).all():
            flags = " ".join(
                f for f in ("admin" if u.is_admin else "", "" if u.is_active else "disattivato") if f
            )
            last = u.last_sync_at.strftime("%Y-%m-%d %H:%M") if u.last_sync_at else "mai"
            rows.append(f"{u.id}\t{u.garmin_email}\tultima sync: {last}\t{flags}")
        return rows
    finally:
        db.close()


def set_admin(email: str) -> bool:
    """Promuove un utente ad amministratore. False se non esiste."""
    init_db()
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.garmin_email == email.strip().lower()))
        if user is None:
            return False
        user.is_admin = True
        db.commit()
        return True
    finally:
        db.close()


def bootstrap_from_env(validate: bool = True) -> str:
    """Crea il primo utente admin dalle credenziali GARMIN_* del .env.

    È il percorso di migrazione dalla v1 single-user: le credenziali che stavano
    nell'ambiente diventano un utente vero, con password cifrata nel DB.
    Il valore della password non viene mai stampato.
    """
    from app.auth.security import encrypt_secret, hash_password

    email = (os.getenv("GARMIN_EMAIL") or "").strip().lower()
    password = os.getenv("GARMIN_PASSWORD") or ""
    if not email or not password:
        return "GARMIN_EMAIL/GARMIN_PASSWORD non presenti nel .env: niente da migrare."

    init_db()
    db = SessionLocal()
    try:
        if db.scalar(select(User).where(User.garmin_email == email)):
            return f"L'utente {email} esiste già."

        if validate:
            from app.garmin.client import validate_credentials

            if not validate_credentials(email, password):
                return f"Garmin ha rifiutato le credenziali di {email}: utente non creato."

        is_first = db.scalar(select(User.id).limit(1)) is None
        db.add(
            User(
                garmin_email=email,
                garmin_password_hash=hash_password(password),
                garmin_password_encrypted=encrypt_secret(password),
                display_name=email.split("@")[0].replace(".", " ").title(),
                is_admin=is_first,
            )
        )
        db.commit()
        return f"Utente {email} creato{' come admin' if is_first else ''}."
    finally:
        db.close()


def generate_vapid_keys() -> str:
    """Genera la coppia di chiavi per le notifiche push, pronte da incollare."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1())

    # La chiave privata va in PKCS8/PEM su una riga (pywebpush la accetta così).
    private_pem = private.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()

    # La pubblica va in base64url del punto non compresso: è ciò che il browser
    # si aspetta come applicationServerKey.
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    public_b64 = base64.urlsafe_b64encode(public_raw).decode().rstrip("=")

    private_flat = private_pem.replace("\n", "\\n")
    return (
        "Aggiungi queste righe al .env:\n\n"
        f"VAPID_PUBLIC_KEY={public_b64}\n"
        f'VAPID_PRIVATE_KEY="{private_flat}"\n'
        "VAPID_CONTACT_EMAIL=tua@email.it\n"
    )


def telegram_webhook_url() -> str:
    """L'URL da registrare presso Telegram, con il comando pronto."""
    from app.config import settings

    if not settings.TELEGRAM_BOT_TOKEN:
        return "TELEGRAM_BOT_TOKEN non impostato nel .env: niente da registrare."

    url = f"{settings.PUBLIC_BASE_URL}/telegram/webhook/{settings.telegram_webhook_secret}"
    return (
        "Registra il webhook con:\n\n"
        f'curl -s "https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}'
        f'/setWebhook?url={url}"\n\n'
        f"URL del webhook: {url}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog="app.cli", description="Amministrazione Garmin Coach")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_invite = sub.add_parser("create-invite", help="genera un codice invito")
    p_invite.add_argument("--expires-days", type=int, default=None)

    sub.add_parser("list-users", help="elenca gli utenti registrati")

    p_admin = sub.add_parser("set-admin", help="promuove un utente ad admin")
    p_admin.add_argument("email")

    p_boot = sub.add_parser(
        "bootstrap-from-env", help="crea il primo utente dalle credenziali nel .env"
    )
    p_boot.add_argument(
        "--no-validate", action="store_true", help="non verificare le credenziali su Garmin"
    )

    sub.add_parser("vapid-keys", help="genera le chiavi per le notifiche push")
    sub.add_parser("telegram-webhook", help="mostra l'URL del webhook Telegram")

    args = parser.parse_args()

    if args.cmd == "create-invite":
        print(create_invite(args.expires_days))
    elif args.cmd == "list-users":
        rows = list_users()
        print("\n".join(rows) if rows else "Nessun utente registrato.")
    elif args.cmd == "set-admin":
        print("Fatto." if set_admin(args.email) else f"Utente {args.email} non trovato.")
    elif args.cmd == "bootstrap-from-env":
        print(bootstrap_from_env(validate=not args.no_validate))
    elif args.cmd == "vapid-keys":
        print(generate_vapid_keys())
    elif args.cmd == "telegram-webhook":
        print(telegram_webhook_url())


if __name__ == "__main__":
    main()
