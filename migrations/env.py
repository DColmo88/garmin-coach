"""Ambiente Alembic.

L'URL del database non sta in alembic.ini ma arriva da `settings.DATABASE_URL`
(quindi dal .env): stessa fonte di verità dell'app, così non si può migrare per
sbaglio il database sbagliato.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, event, pool

from app.config import settings
from app.db import models  # noqa: F401 — l'import registra i modelli su Base
from app.db.database import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # SQLite non sa alterare una chiave esterna: Alembic ricrea la tabella da
    # capo (`render_as_batch`), il che vuol dire lasciarne cadere una che altre
    # referenziano. Con il controllo delle chiavi acceso — e dalla v3 lo è, per
    # far valere `ON DELETE CASCADE` anche in sviluppo — quella `DROP TABLE`
    # fallirebbe. Si spegne per la durata della migrazione.
    #
    # Va spento **alla connessione** e non con una query dopo: `exec_driver_sql`
    # su una connessione appena aperta apre anche una transazione che Alembic
    # non sa di avere, e a fine migrazione nessuno la chiude. Il risultato è
    # una migrazione che scrive le tabelle e non scrive `alembic_version`.
    # Questo ascoltatore gira dopo quello di `app.db.database`, che lo accende,
    # perché è registrato dopo.
    @event.listens_for(connectable, "connect")
    def _relax_sqlite_foreign_keys(dbapi_connection, _record):
        if connectable.dialect.name != "sqlite":
            return
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # batch mode: SQLite non sa fare ALTER TABLE, Alembic ricrea la tabella
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
