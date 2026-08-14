"""Crittografia delle credenziali.

Due usi distinti della stessa password Garmin:
- **bcrypt** (hash a senso unico) per verificare il login senza contattare Garmin;
- **Fernet** (cifratura reversibile) perché la sync notturna deve poter rifare
  il login quando il token OAuth scade.

La chiave Fernet vive solo nel .env del server: senza quella, i dati cifrati
nel DB sono inutilizzabili.
"""
from __future__ import annotations

import bcrypt
from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    if not settings.FERNET_KEY:
        raise RuntimeError(
            "FERNET_KEY mancante nel .env — genera con: "
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.FERNET_KEY.encode())


def encrypt_secret(plain: str) -> str:
    """Cifra un segreto (reversibile con la stessa FERNET_KEY)."""
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    """Decifra un segreto prodotto da `encrypt_secret`."""
    return _fernet().decrypt(token.encode()).decode()


def hash_password(plain: str) -> str:
    """Hash bcrypt per la verifica del login."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """True se la password corrisponde all'hash. Mai solleva per hash malformati."""
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False
