"""Setup SQLAlchemy: engine, sessione e base dei modelli.

Usa SQLite ora. Per passare a Postgres (es. su Hetzner) basta cambiare
DATABASE_URL nel file .env: il resto del codice non cambia.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

# Assicura che la cartella data/ esista per SQLite
if settings.DATABASE_URL.startswith("sqlite"):
    Path("./data").mkdir(parents=True, exist_ok=True)

# check_same_thread serve solo a SQLite con più thread (uvicorn)
connect_args = (
    {"check_same_thread": False}
    if settings.DATABASE_URL.startswith("sqlite")
    else {}
)

engine = create_engine(settings.DATABASE_URL, connect_args=connect_args, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


@event.listens_for(Engine, "connect")
def _enforce_foreign_keys(dbapi_connection, connection_record) -> None:
    """SQLite ignora le chiavi esterne se non glielo si chiede.

    Postgres applica `ON DELETE CASCADE` sempre; SQLite lo fa solo con il
    pragma acceso, e di default è spento. Senza questa riga la cancellazione di
    un account passerebbe i test su SQLite lasciando dietro di sé dodici
    tabelle di orfani, e si comporterebbe in modo diverso in produzione — che è
    la categoria di bug che questo progetto ha già incontrato due volte.

    L'ascolto è sulla classe `Engine` e non sull'istanza qui sopra perché i
    test costruiscono un engine loro: legandolo a questo, il pragma sarebbe
    acceso in produzione e spento proprio dove serve verificarlo.
    """
    import sqlite3

    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


class Base(DeclarativeBase):
    """Base dichiarativa per tutti i modelli ORM."""


def init_db() -> None:
    """Crea le tabelle se non esistono."""
    from app.db import models  # noqa: F401  (registra i modelli)

    Base.metadata.create_all(bind=engine)


def get_session():
    """Dependency FastAPI: fornisce una sessione DB e la chiude a fine richiesta."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
