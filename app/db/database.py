"""Setup SQLAlchemy: engine, sessione e base dei modelli.

Usa SQLite ora. Per passare a Postgres (es. su Hetzner) basta cambiare
DATABASE_URL nel file .env: il resto del codice non cambia.
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine
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
