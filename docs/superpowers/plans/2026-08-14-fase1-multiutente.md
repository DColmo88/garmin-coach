# Fase 1 — Fondamenta Multi-utente · Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trasformare l'app single-user in multi-utente: login con credenziali Garmin, registrazione a invito, sessioni cookie, dati partizionati per `user_id`, client Garmin per-utente, migrazioni Alembic.

**Architecture:** Le credenziali Garmin sono il login: bcrypt per la verifica locale, Fernet per la copia usata dalla sync. Sessione = cookie firmato (itsdangerous). Ogni tabella dati riceve `user_id` FK con vincoli unici compositi. Il singleton Garmin diventa un registry per-utente con tokenstore separato. Alembic gestisce lo schema in prod; i test usano `create_all` su SQLite temporaneo.

**Tech Stack:** FastAPI, SQLAlchemy 2 (Mapped/mapped_column), Alembic, cryptography (Fernet), bcrypt, itsdangerous, Jinja2, pytest + httpx.

## Global Constraints

- Python 3.11 — eseguire sempre `./venv/bin/pytest` e `./venv/bin/alembic` (mai i binari globali).
- Copy UI in italiano; stile dark design system esistente (tokens in `style.css`, template estendono `base.html`).
- Parsing/gestione errori difensivi: mai far crashare una pagina per dati mancanti.
- Mai committare `.env`, `data/`, credenziali.
- I test non chiamano mai Garmin né provider AI reali: sempre fake/monkeypatch.
- Messaggi di commit: convenzione `feat:`/`fix:`/`chore:`/`test:` come da history.

---

### Task 1: Dipendenze e configurazione (secrets di auth)

**Files:**
- Modify: `requirements.txt`
- Modify: `app/config.py`
- Modify: `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `settings.SESSION_SECRET: str`, `settings.FERNET_KEY: str` (base64 urlsafe 32 byte), usati dai Task 4-5.

- [ ] **Step 1: Aggiungi dipendenze a `requirements.txt`**

```
alembic==1.14.0
cryptography==44.0.0
bcrypt==4.2.1
itsdangerous==2.2.0
```

- [ ] **Step 2: Installa**

Run: `./venv/bin/pip install -r requirements.txt`

- [ ] **Step 3: Scrivi il test**

```python
"""tests/test_config.py"""
import importlib


def test_settings_expose_auth_secrets(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "s3cret")
    monkeypatch.setenv("FERNET_KEY", "k" * 44)
    import app.config
    importlib.reload(app.config)
    assert app.config.settings.SESSION_SECRET == "s3cret"
    assert app.config.settings.FERNET_KEY == "k" * 44
```

- [ ] **Step 4: Run test → FAIL** (`AttributeError: SESSION_SECRET`)

Run: `./venv/bin/pytest tests/test_config.py -v`

- [ ] **Step 5: Implementa in `app/config.py`** (dentro `class Settings`, dopo `DATABASE_URL`)

```python
    # Auth multi-utente
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "dev-only-change-me")
    FERNET_KEY: str = os.getenv("FERNET_KEY", "")
```

In `.env.example` aggiungi (con commento su come generarli):

```
# Auth (genera con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
SESSION_SECRET=
FERNET_KEY=
```

- [ ] **Step 6: Run test → PASS, poi commit**

```bash
git add requirements.txt app/config.py .env.example tests/test_config.py
git commit -m "feat: dipendenze e settings per auth multi-utente"
```

---

### Task 2: Modelli User/InviteCode + `user_id` su tutte le tabelle dati

**Files:**
- Modify: `app/db/models.py`
- Test: `tests/test_models_multiuser.py`

**Interfaces:**
- Produces: `User(id, garmin_email, garmin_password_encrypted, garmin_password_hash, display_name, is_admin, timezone, ai_quota_chat_daily, created_at)`, `InviteCode(id, code, created_by_id, used_by_id, used_at, expires_at)`.
- Produces: ogni tabella dati ha `user_id: Mapped[int]` FK `users.id`; vincoli unici compositi `(user_id, day)` e `(user_id, garmin_activity_id)`.

- [ ] **Step 1: Scrivi il test**

```python
"""tests/test_models_multiuser.py"""
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import InviteCode, SleepRecord, User


def make_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_same_day_allowed_for_different_users():
    db = make_session()
    u1 = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    u2 = User(garmin_email="b@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add_all([u1, u2])
    db.flush()
    db.add_all([
        SleepRecord(user_id=u1.id, day=date(2026, 8, 1)),
        SleepRecord(user_id=u2.id, day=date(2026, 8, 1)),
    ])
    db.commit()
    assert db.query(SleepRecord).count() == 2


def test_invite_code_defaults():
    db = make_session()
    inv = InviteCode(code="ABC123")
    db.add(inv)
    db.commit()
    assert inv.used_by_id is None and inv.used_at is None
```

- [ ] **Step 2: Run → FAIL** (`ImportError: User`)

Run: `./venv/bin/pytest tests/test_models_multiuser.py -v`

- [ ] **Step 3: Implementa in `app/db/models.py`**

Aggiungi in testa agli import: `from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint` e `from datetime import date, datetime, timezone as tz`.

Nuovi modelli (prima di `Activity`):

```python
class User(Base):
    """Utente dell'app: le credenziali Garmin sono anche il login."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    garmin_email: Mapped[str] = mapped_column(String, unique=True, index=True)
    garmin_password_encrypted: Mapped[str] = mapped_column(String)  # Fernet, per la sync
    garmin_password_hash: Mapped[str] = mapped_column(String)       # bcrypt, per il login
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    timezone: Mapped[str] = mapped_column(String, default="Europe/Rome")
    ai_quota_chat_daily: Mapped[int] = mapped_column(Integer, default=30)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InviteCode(Base):
    """Codice invito per la registrazione a cerchia ristretta."""

    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String, unique=True, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    used_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
```

Per ciascuna delle 5 tabelle dati:
- aggiungi `user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)`
- `Activity`: rimuovi `unique=True` da `garmin_activity_id`, aggiungi `__table_args__ = (UniqueConstraint("user_id", "garmin_activity_id"),)`
- `SleepRecord`, `TrainingMetric`, `DailyWellness`, `BodyComposition`: rimuovi `unique=True` da `day` (lascia `index=True`), aggiungi `__table_args__ = (UniqueConstraint("user_id", "day"),)`

- [ ] **Step 4: Run → PASS.** Nota: gli altri test esistenti ora falliranno (sync/queries senza user) — è atteso, li sistemano i Task 6-8. Esegui solo questo file.

- [ ] **Step 5: Commit**

```bash
git add app/db/models.py tests/test_models_multiuser.py
git commit -m "feat: modelli User/InviteCode + user_id su tutte le tabelle dati"
```

---

### Task 3: Alembic (migrazioni schema)

**Files:**
- Create: `alembic.ini`, `migrations/env.py`, `migrations/versions/<rev>_schema_v2_multiutente.py`
- Modify: `app/db/database.py` (docstring: prod usa Alembic), `CLAUDE.md` (nota migrazioni)
- Test: `tests/test_migrations.py`

**Interfaces:**
- Produces: `alembic upgrade head` crea lo schema completo su DB vuoto. `init_db()` resta per dev/test.

- [ ] **Step 1: Inizializza**

Run: `cd "/Users/davidecolmo/Documents/DAVIDE/Progetti/Garmin Connector" && ./venv/bin/alembic init migrations`

- [ ] **Step 2: Configura `migrations/env.py`** — sostituisci la sezione config con:

```python
from app.config import settings
from app.db.database import Base
from app.db import models  # noqa: F401 — registra i modelli

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata
```

In `alembic.ini` lascia `sqlalchemy.url` vuoto (viene da env.py).

- [ ] **Step 3: Genera la migrazione iniziale**

Run: `DATABASE_URL=sqlite:////tmp/alembic_gen.db ./venv/bin/alembic revision --autogenerate -m "schema v2 multiutente"`
(DB temporaneo vuoto → l'autogenerate produce l'intero schema. Verifica a occhio che contenga `users`, `invite_codes` e i vincoli compositi.)

- [ ] **Step 4: Scrivi il test**

```python
"""tests/test_migrations.py"""
import subprocess
import sys

from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_head_builds_schema(tmp_path):
    db = tmp_path / "mig.db"
    url = f"sqlite:///{db}"
    res = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env={"DATABASE_URL": url, "PATH": "", "SESSION_SECRET": "t", "FERNET_KEY": ""},
        capture_output=True, text=True, cwd=".",
    )
    assert res.returncode == 0, res.stderr
    tables = set(inspect(create_engine(url)).get_table_names())
    assert {"users", "invite_codes", "activities", "sleep_records"} <= tables
```

- [ ] **Step 5: Run → PASS** (se fallisce, sistemare env.py finché passa)

Run: `./venv/bin/pytest tests/test_migrations.py -v`

- [ ] **Step 6: Commit**

```bash
git add alembic.ini migrations tests/test_migrations.py app/db/database.py CLAUDE.md
git commit -m "feat: Alembic con migrazione iniziale schema v2"
```

---

### Task 4: Security helpers (Fernet + bcrypt)

**Files:**
- Create: `app/auth/__init__.py` (vuoto), `app/auth/security.py`
- Test: `tests/test_security.py`

**Interfaces:**
- Produces: `encrypt_secret(plain: str) -> str`, `decrypt_secret(token: str) -> str`, `hash_password(plain: str) -> str`, `verify_password(plain: str, hashed: str) -> bool`.

- [ ] **Step 1: Test**

```python
"""tests/test_security.py"""
from cryptography.fernet import Fernet


def test_encrypt_roundtrip(monkeypatch):
    monkeypatch.setattr("app.config.settings.FERNET_KEY", Fernet.generate_key().decode())
    from app.auth import security
    token = security.encrypt_secret("password-garmin")
    assert token != "password-garmin"
    assert security.decrypt_secret(token) == "password-garmin"


def test_password_hash_roundtrip():
    from app.auth import security
    h = security.hash_password("s3gret0!")
    assert security.verify_password("s3gret0!", h)
    assert not security.verify_password("sbagliata", h)
```

- [ ] **Step 2: Run → FAIL** (`ModuleNotFoundError`)

- [ ] **Step 3: Implementa `app/auth/security.py`**

```python
"""Crittografia credenziali: Fernet per la copia sync, bcrypt per il login."""
from __future__ import annotations

import bcrypt
from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    if not settings.FERNET_KEY:
        raise RuntimeError("FERNET_KEY mancante nel .env")
    return Fernet(settings.FERNET_KEY.encode())


def encrypt_secret(plain: str) -> str:
    return _fernet().encrypt(plain.encode()).decode()


def decrypt_secret(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False
```

- [ ] **Step 4: Run → PASS · Step 5: Commit**

```bash
git add app/auth tests/test_security.py
git commit -m "feat: security helpers Fernet + bcrypt"
```

---

### Task 5: Sessioni cookie + dependency `require_user`

**Files:**
- Create: `app/auth/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Produces: `create_session_token(user_id: int) -> str`, `read_session_token(token: str) -> int | None` (scadenza 30gg), `SESSION_COOKIE = "gc_session"`, `LoginRequired(Exception)`, dependency FastAPI `require_user(request, db) -> User` (solleva `LoginRequired` se assente/invalidə), `optional_user(request, db) -> User | None`.
- Consumes: `User` (Task 2).

- [ ] **Step 1: Test**

```python
"""tests/test_session.py"""
import pytest

from app.auth.session import create_session_token, read_session_token


def test_token_roundtrip():
    assert read_session_token(create_session_token(42)) == 42


def test_tampered_token_rejected():
    assert read_session_token(create_session_token(42) + "x") is None
    assert read_session_token("garbage") is None
```

- [ ] **Step 2: Run → FAIL · Step 3: Implementa `app/auth/session.py`**

```python
"""Sessioni: cookie firmato con itsdangerous, dependency FastAPI."""
from __future__ import annotations

from fastapi import Depends, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.db.database import get_session
from app.db.models import User

SESSION_COOKIE = "gc_session"
MAX_AGE_SEC = 30 * 24 * 3600


class LoginRequired(Exception):
    """Richiesta senza sessione valida: il handler redirige a /login."""


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SESSION_SECRET, salt="gc-session")


def create_session_token(user_id: int) -> str:
    return _serializer().dumps({"uid": user_id})


def read_session_token(token: str) -> int | None:
    try:
        return _serializer().loads(token, max_age=MAX_AGE_SEC)["uid"]
    except (BadSignature, SignatureExpired, KeyError, TypeError):
        return None


def optional_user(request: Request, db: Session = Depends(get_session)) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    uid = read_session_token(token)
    return db.get(User, uid) if uid is not None else None


def require_user(request: Request, db: Session = Depends(get_session)) -> User:
    user = optional_user(request, db)
    if user is None:
        raise LoginRequired()
    return user
```

- [ ] **Step 4: Run → PASS · Step 5: Commit**

```bash
git add app/auth/session.py tests/test_session.py
git commit -m "feat: sessioni cookie firmate + dependency require_user"
```

---

### Task 6: Auth service (login = credenziali Garmin, registrazione a invito)

**Files:**
- Create: `app/auth/service.py`
- Test: `tests/test_auth_service.py`

**Interfaces:**
- Produces: `authenticate(db, email, password, invite_code=None, garmin_validator=validate_credentials) -> User`; eccezioni `InvalidCredentials(Exception)`, `InviteRequired(Exception)`, `InvalidInvite(Exception)`. Primo utente registrato → `is_admin=True`.
- Consumes: security (Task 4), modelli (Task 2), `validate_credentials` (Task 7 — qui importato lazy, nei test sempre iniettato).

- [ ] **Step 1: Test**

```python
"""tests/test_auth_service.py"""
from datetime import datetime

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import InviteCode, User


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr("app.config.settings.FERNET_KEY", Fernet.generate_key().decode())
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def ok_validator(email, password):
    return True


def ko_validator(email, password):
    return False


def test_new_user_requires_invite(db):
    from app.auth import service
    with pytest.raises(service.InviteRequired):
        service.authenticate(db, "a@x.it", "pw", garmin_validator=ok_validator)


def test_registration_with_invite_creates_admin_first_user(db):
    from app.auth import service
    db.add(InviteCode(code="INV1"))
    db.commit()
    user = service.authenticate(db, "a@x.it", "pw", invite_code="INV1",
                                garmin_validator=ok_validator)
    assert user.is_admin is True
    inv = db.query(InviteCode).one()
    assert inv.used_by_id == user.id and inv.used_at is not None
    # lo stesso invito non è riusabile
    with pytest.raises(service.InvalidInvite):
        service.authenticate(db, "b@x.it", "pw", invite_code="INV1",
                             garmin_validator=ok_validator)


def test_registration_rejected_if_garmin_refuses(db):
    from app.auth import service
    db.add(InviteCode(code="INV2"))
    db.commit()
    with pytest.raises(service.InvalidCredentials):
        service.authenticate(db, "a@x.it", "pw", invite_code="INV2",
                             garmin_validator=ko_validator)


def test_existing_user_login_and_password_self_healing(db):
    from app.auth import service
    db.add(InviteCode(code="INV3"))
    db.commit()
    service.authenticate(db, "a@x.it", "vecchia", invite_code="INV3",
                         garmin_validator=ok_validator)
    # login normale
    assert service.authenticate(db, "a@x.it", "vecchia",
                                garmin_validator=ko_validator)
    # password cambiata su Garmin: hash locale fallisce ma Garmin conferma → aggiorna
    user = service.authenticate(db, "a@x.it", "nuova", garmin_validator=ok_validator)
    from app.auth.security import verify_password
    assert verify_password("nuova", user.garmin_password_hash)
    # password sbagliata ovunque → errore
    with pytest.raises(service.InvalidCredentials):
        service.authenticate(db, "a@x.it", "sbagliata", garmin_validator=ko_validator)
```

- [ ] **Step 2: Run → FAIL · Step 3: Implementa `app/auth/service.py`**

```python
"""Login/registrazione: le credenziali Garmin sono il login dell'app."""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import encrypt_secret, hash_password, verify_password
from app.db.models import InviteCode, User


class InvalidCredentials(Exception):
    """Credenziali rifiutate (localmente e da Garmin)."""


class InviteRequired(Exception):
    """Email sconosciuta e nessun codice invito fornito."""


class InvalidInvite(Exception):
    """Codice invito inesistente, usato o scaduto."""


def _default_validator(email: str, password: str) -> bool:
    from app.garmin.client import validate_credentials
    return validate_credentials(email, password)


def authenticate(
    db: Session,
    email: str,
    password: str,
    invite_code: str | None = None,
    garmin_validator: Callable[[str, str], bool] = _default_validator,
) -> User:
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.garmin_email == email))

    if user is not None:
        if verify_password(password, user.garmin_password_hash):
            return user
        # self-healing: forse la password Garmin è cambiata
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
    if invite is None or invite.used_by_id is not None or (
        invite.expires_at is not None and invite.expires_at < now
    ):
        raise InvalidInvite()
    if not garmin_validator(email, password):
        raise InvalidCredentials()

    is_first = db.scalar(select(User.id).limit(1)) is None
    user = User(
        garmin_email=email,
        garmin_password_hash=hash_password(password),
        garmin_password_encrypted=encrypt_secret(password),
        display_name=email.split("@")[0],
        is_admin=is_first,
    )
    db.add(user)
    db.flush()
    invite.used_by_id = user.id
    invite.used_at = now
    db.commit()
    return user
```

- [ ] **Step 4: Run → PASS · Step 5: Commit**

```bash
git add app/auth/service.py tests/test_auth_service.py
git commit -m "feat: auth service — login Garmin, invito, self-healing password"
```

---

### Task 7: Client Garmin per-utente (registry)

**Files:**
- Modify: `app/garmin/client.py` (riscrittura)
- Test: `tests/test_garmin_client.py`

**Interfaces:**
- Produces: `get_client(user: User) -> Garmin` (cache per `user.id`, tokenstore `{settings.GARMIN_TOKENSTORE}/{user.id}`, lock per utente), `reset_client(user_id: int) -> None`, `validate_credentials(email: str, password: str) -> bool`, `GarminClientError` invariato.
- Consumes: `decrypt_secret` (Task 4), `User` (Task 2).

- [ ] **Step 1: Test**

```python
"""tests/test_garmin_client.py"""
import pytest
from cryptography.fernet import Fernet


class FakeGarmin:
    instances = []

    def __init__(self, email=None, password=None):
        self.email = email
        self.password = password
        self.logged_in = False
        FakeGarmin.instances.append(self)

    def login(self, tokenstore=None):
        if tokenstore is not None:
            raise Exception("nessun token salvato")  # forza il login completo
        if self.password == "wrong":
            raise Exception("bad credentials")
        self.logged_in = True

    class garth:  # noqa: N801 — imita l'attributo della lib reale
        @staticmethod
        def dump(path):
            pass


@pytest.fixture(autouse=True)
def patch_garmin(monkeypatch, tmp_path):
    monkeypatch.setattr("app.garmin.client.Garmin", FakeGarmin)
    monkeypatch.setattr("app.config.settings.FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("app.config.settings.GARMIN_TOKENSTORE", str(tmp_path))
    from app.garmin import client
    client._clients.clear()
    FakeGarmin.instances.clear()


def make_user(uid, email="a@x.it", password="pw"):
    from app.auth.security import encrypt_secret
    from app.db.models import User
    u = User(garmin_email=email, garmin_password_encrypted=encrypt_secret(password),
             garmin_password_hash="h")
    u.id = uid
    return u


def test_get_client_is_cached_per_user():
    from app.garmin import client
    u1, u2 = make_user(1), make_user(2, email="b@x.it")
    c1 = client.get_client(u1)
    assert client.get_client(u1) is c1
    assert client.get_client(u2) is not c1


def test_validate_credentials():
    from app.garmin import client
    assert client.validate_credentials("a@x.it", "pw") is True
    assert client.validate_credentials("a@x.it", "wrong") is False
```

- [ ] **Step 2: Run → FAIL · Step 3: Riscrivi `app/garmin/client.py`**

```python
"""Client Garmin per-utente con cache token su disco e registry in memoria."""
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
    """Client autenticato per l'utente: riusa token salvato, altrimenti login."""
    if user.id in _clients:
        return _clients[user.id]
    with _locks[user.id]:
        if user.id in _clients:  # double-check dopo il lock
            return _clients[user.id]
        tokenstore = _tokenstore(user.id)
        try:
            client = Garmin()
            client.login(tokenstore)
            _clients[user.id] = client
            return client
        except Exception as exc:
            logger.info("Token utente %s non riutilizzabile (%s), login completo.", user.id, exc)
        try:
            password = decrypt_secret(user.garmin_password_encrypted)
            client = Garmin(email=user.garmin_email, password=password)
            client.login()
            client.garth.dump(tokenstore)
            _clients[user.id] = client
            return client
        except Exception as exc:
            raise GarminClientError(f"Login Garmin fallito per {user.garmin_email}: {exc}") from exc


def reset_client(user_id: int) -> None:
    _clients.pop(user_id, None)


def validate_credentials(email: str, password: str) -> bool:
    """Verifica le credenziali con un login reale (usato in registrazione)."""
    try:
        client = Garmin(email=email, password=password)
        client.login()
        return True
    except Exception:
        return False
```

- [ ] **Step 4: Run → PASS · Step 5: Commit**

```bash
git add app/garmin/client.py tests/test_garmin_client.py
git commit -m "feat: registry client Garmin per-utente"
```

---

### Task 8: Sync e queries per-utente

**Files:**
- Modify: `app/garmin/sync.py`, `app/queries.py`, `app/garmin/service.py`
- Test: `tests/test_sync_multiuser.py` + aggiorna `tests/test_coach_snapshot.py`

**Interfaces:**
- Produces: `sync_all(db, user, activity_limit=50, days=28)` e tutte le `sync_*(db, user, ...)`; `_upsert(db, model, user_id, day)`; in `queries.py` tutte le funzioni di lettura prendono `user_id: int` come secondo argomento (`recent_activities(db, user_id, limit=50)`, `wellness_series(db, user_id, days=28)`, `sleep_series`, `training_series`, `body_series`, `get_activity(db, user_id, activity_id)`, `coach_snapshot(db, user_id)`); `service.py`: le funzioni live prendono `user` e la cache in memoria è keyed per `user.id`.
- Consumes: `get_client(user)` (Task 7).

- [ ] **Step 1: Test isolamento**

```python
"""tests/test_sync_multiuser.py"""
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import SleepRecord, User
from app import queries as q


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def seed_users(db):
    u1 = User(garmin_email="a@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    u2 = User(garmin_email="b@x.it", garmin_password_encrypted="e", garmin_password_hash="h")
    db.add_all([u1, u2]); db.commit()
    return u1, u2


def test_queries_are_isolated_per_user(db):
    u1, u2 = seed_users(db)
    db.add(SleepRecord(user_id=u1.id, day=date(2026, 8, 1), sleep_score=80))
    db.add(SleepRecord(user_id=u2.id, day=date(2026, 8, 1), sleep_score=40))
    db.commit()
    s1 = q.sleep_series(db, u1.id, 28)
    assert [r.sleep_score for r in s1] == [80]
    snap = q.coach_snapshot(db, u2.id)
    assert snap["sleep_score"] == 40


def test_upsert_scoped_to_user(db):
    from app.garmin.sync import _upsert
    u1, u2 = seed_users(db)
    row1, new1 = _upsert(db, SleepRecord, u1.id, date(2026, 8, 2))
    db.commit()
    row2, new2 = _upsert(db, SleepRecord, u2.id, date(2026, 8, 2))
    assert new1 and new2 and row1 is not row2
    row1b, new1b = _upsert(db, SleepRecord, u1.id, date(2026, 8, 2))
    assert not new1b and row1b.id == row1.id
```

- [ ] **Step 2: Run → FAIL**

- [ ] **Step 3: Modifica `app/garmin/sync.py`**

- `_upsert(db, model, user_id: int, day: date)`: `where(model.user_id == user_id, model.day == day)`; alla creazione `model(user_id=user_id, day=day)`.
- Ogni `sync_*(db, user, ...)`: firma con `user: User`, `client = get_client(user)`, tutti i `_upsert(db, Model, user.id, day)`; in `sync_activities` la ricerca esistente diventa `where(Activity.user_id == user.id, Activity.garmin_activity_id == gid)` e i nuovi record ricevono `user_id=user.id`.
- `sync_all(db, user, activity_limit=50, days=28)` passa `user` a tutte.

- [ ] **Step 4: Modifica `app/queries.py`** — ogni funzione riceve `user_id: int` dopo `db` e aggiunge `.where(Model.user_id == user_id)` alla select. `coach_snapshot(db, user_id)` propaga alle serie interne.

- [ ] **Step 5: Modifica `app/garmin/service.py`** — le funzioni live ricevono `user: User`, usano `get_client(user)`, e la chiave cache diventa `f"{user.id}:{nome}"`. `clear_cache(user_id: int | None = None)`: svuota solo le chiavi dell'utente se fornito.

- [ ] **Step 6: Aggiorna `tests/test_coach_snapshot.py`** — le chiamate `coach_snapshot(db)` diventano `coach_snapshot(db, user.id)` con un utente seed (riusa `seed_users`).

- [ ] **Step 7: Run tutta la suite → PASS** (tranne `test_coach_page.py`, sistemato al Task 9)

Run: `./venv/bin/pytest -v`

- [ ] **Step 8: Commit**

```bash
git add app/garmin/sync.py app/queries.py app/garmin/service.py tests/
git commit -m "feat: sync, queries e service live partizionati per utente"
```

---

### Task 9: Route login/logout + protezione pagine + UI

**Files:**
- Create: `app/routers/auth.py`, `app/templates/login.html`
- Modify: `app/main.py`, `app/routers/pages.py`, `app/templates/base.html`
- Test: `tests/test_auth_routes.py` + aggiorna `tests/test_coach_page.py`
- Create: `tests/conftest.py`

**Interfaces:**
- Produces: `GET /login` (form), `POST /login` (campi `email`, `password`, `invite_code` opzionale) → cookie + redirect `/coach`; `POST /logout` → cancella cookie → `/login`. Tutte le pagine e `/sync` richiedono `require_user`; `LoginRequired` → redirect 303 a `/login`. `_ctx()` include `user`.
- Consumes: Task 5, 6, 8.

- [ ] **Step 1: Crea `tests/conftest.py`** (fixture condivise: app di test con DB temporaneo e validator fake)

```python
"""Fixture condivise: app FastAPI con DB SQLite temporaneo e Garmin finto."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setattr("app.config.settings.FERNET_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr("app.config.settings.SESSION_SECRET", "test-secret")
    monkeypatch.setattr("app.config.settings.DATABASE_URL",
                        f"sqlite:///{tmp_path}/test.db")
    # ricrea engine/session sul DB di test
    import importlib
    import app.db.database as database
    importlib.reload(database)
    import app.db.models  # noqa: F401
    database.init_db()
    # il validator Garmin accetta tutto tranne password "wrong"
    monkeypatch.setattr("app.auth.service._default_validator",
                        lambda email, password: password != "wrong")
    from app.main import app as fastapi_app
    return TestClient(fastapi_app, follow_redirects=False)


@pytest.fixture()
def logged_client(client):
    from app.db.database import SessionLocal
    from app.db.models import InviteCode
    db = SessionLocal()
    db.add(InviteCode(code="TEST-INVITE")); db.commit(); db.close()
    r = client.post("/login", data={"email": "test@x.it", "password": "pw",
                                    "invite_code": "TEST-INVITE"})
    assert r.status_code == 303
    return client
```

Nota: se il reload di `app.db.database` crea problemi con moduli già importati, in alternativa usare dependency override di `get_session` sull'app — scegliere la strada che fa passare i test in modo pulito.

- [ ] **Step 2: Test route**

```python
"""tests/test_auth_routes.py"""

def test_pages_redirect_to_login_when_anonymous(client):
    for path in ["/", "/coach", "/sleep"]:
        r = client.get(path)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Garmin" in r.text


def test_login_flow_sets_cookie_and_opens_coach(logged_client):
    r = logged_client.get("/coach")
    assert r.status_code == 200


def test_wrong_credentials_show_error(client):
    from app.db.database import SessionLocal
    from app.db.models import InviteCode
    db = SessionLocal(); db.add(InviteCode(code="INV")); db.commit(); db.close()
    r = client.post("/login", data={"email": "x@x.it", "password": "wrong",
                                    "invite_code": "INV"})
    assert r.status_code == 200 and "credenziali" in r.text.lower()


def test_logout_clears_session(logged_client):
    r = logged_client.post("/logout")
    assert r.status_code == 303
    assert logged_client.get("/coach").status_code == 303
```

- [ ] **Step 3: Run → FAIL · Step 4: Implementa `app/routers/auth.py`**

```python
"""Route di autenticazione: login (Garmin), registrazione a invito, logout."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.session import SESSION_COOKIE, MAX_AGE_SEC, create_session_token
from app.db.database import get_session
from app.templating import templates

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    invite_code: str = Form(""),
    db: Session = Depends(get_session),
):
    try:
        user = service.authenticate(db, email, password,
                                    invite_code=invite_code.strip() or None)
    except service.InviteRequired:
        return templates.TemplateResponse("login.html", {
            "request": request, "email": email, "need_invite": True,
            "error": "Primo accesso: serve un codice invito.",
        })
    except service.InvalidInvite:
        return templates.TemplateResponse("login.html", {
            "request": request, "email": email, "need_invite": True,
            "error": "Codice invito non valido o già usato.",
        })
    except service.InvalidCredentials:
        return templates.TemplateResponse("login.html", {
            "request": request, "email": email,
            "error": "Credenziali Garmin non valide.",
        })
    resp = RedirectResponse("/coach", status_code=303)
    resp.set_cookie(SESSION_COOKIE, create_session_token(user.id),
                    max_age=MAX_AGE_SEC, httponly=True, samesite="lax")
    return resp


@router.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(SESSION_COOKIE)
    return resp
```

- [ ] **Step 5: Template `app/templates/login.html`** — pagina standalone (non estende base: niente sidebar), stessa palette dark del design system: card centrata, logo/nome app, form email+password, campo invito (visibile se `need_invite`), messaggio `error` se presente, microcopy che spiega che si usano le credenziali Garmin Connect e che sono cifrate. Bottone primario accent. Curare l'estetica (gradiente sfondo, ring/logo).

- [ ] **Step 6: Integra in `app/main.py`**

```python
from fastapi.responses import RedirectResponse
from app.auth.session import LoginRequired
from app.routers import auth as auth_router

app.include_router(auth_router.router)


@app.exception_handler(LoginRequired)
def _login_required(request, exc):
    return RedirectResponse("/login", status_code=303)
```

`/sync`, `/api/context`, `/ai/plan`: aggiungi `user: User = Depends(require_user)` e passa `user`/`user.id` a `sync_all(db, user)`, `build_ai_context(db, user.id)`, `service.clear_cache(user.id)`. (`build_ai_context`: aggiungi il parametro `user_id` e propagalo alle query interne.)

- [ ] **Step 7: Proteggi `app/routers/pages.py`** — in ogni route: `user: User = Depends(require_user)`; le chiamate a `q.*`/`service.*` ricevono `user.id`/`user`; `_ctx(request, active, user, **extra)` include `"user": user`. In `base.html` topbar/sidebar: nome utente + form POST logout.

- [ ] **Step 8: Aggiorna `tests/test_coach_page.py`** per usare la fixture `logged_client`.

- [ ] **Step 9: Run tutta la suite → PASS**

Run: `./venv/bin/pytest -v`

- [ ] **Step 10: Commit**

```bash
git add app/routers app/templates app/main.py tests/
git commit -m "feat: login/logout, protezione pagine, UI accesso"
```

---

### Task 10: CLI inviti + smoke test manuale

**Files:**
- Create: `app/cli.py`
- Modify: `CLAUDE.md` (sezione "Multi-utente" con flusso invito)
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `python -m app.cli create-invite [--expires-days N]` stampa un codice invito; `python -m app.cli list-users` elenca gli utenti.

- [ ] **Step 1: Test**

```python
"""tests/test_cli.py"""

def test_create_invite_generates_code(client):
    from app.cli import create_invite
    from app.db.database import SessionLocal
    from app.db.models import InviteCode
    code = create_invite(expires_days=7)
    db = SessionLocal()
    assert db.query(InviteCode).filter_by(code=code).one().expires_at is not None
    db.close()
```

- [ ] **Step 2: Run → FAIL · Step 3: Implementa `app/cli.py`**

```python
"""Piccola CLI amministrativa: python -m app.cli <comando>."""
from __future__ import annotations

import argparse
import secrets
from datetime import datetime, timedelta

from app.db.database import SessionLocal, init_db
from app.db.models import InviteCode, User


def create_invite(expires_days: int | None = None) -> str:
    init_db()
    code = secrets.token_urlsafe(8)
    db = SessionLocal()
    expires = datetime.utcnow() + timedelta(days=expires_days) if expires_days else None
    db.add(InviteCode(code=code, expires_at=expires))
    db.commit()
    db.close()
    return code


def list_users() -> list[str]:
    init_db()
    db = SessionLocal()
    rows = [f"{u.id}\t{u.garmin_email}\t{'admin' if u.is_admin else ''}"
            for u in db.query(User).all()]
    db.close()
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_inv = sub.add_parser("create-invite")
    p_inv.add_argument("--expires-days", type=int, default=None)
    sub.add_parser("list-users")
    args = parser.parse_args()
    if args.cmd == "create-invite":
        print(create_invite(args.expires_days))
    elif args.cmd == "list-users":
        print("\n".join(list_users()))
```

- [ ] **Step 4: Run suite completa → PASS · Step 5: Smoke test manuale**

Run: `./venv/bin/python -m app.cli create-invite` poi `./venv/bin/uvicorn app.main:app --reload` → aprire `http://localhost:8000` → redirect a `/login` → registrarsi con l'invito (credenziali Garmin reali dal `.env` di Davide) → vedere `/coach` → sync manuale → dati presenti. NOTA: rigenerare prima il DB locale (`rm data/garmin_connector.db`) perché lo schema è cambiato.

- [ ] **Step 6: Aggiorna `CLAUDE.md`** (login a invito, CLI, Alembic) **e commit**

```bash
git add app/cli.py tests/test_cli.py CLAUDE.md
git commit -m "feat: CLI inviti + docs multi-utente"
```
