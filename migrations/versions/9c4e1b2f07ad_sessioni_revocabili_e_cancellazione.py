"""Sessioni revocabili e cancellazione dell'account

Due cose che vanno insieme perché rispondono alla stessa domanda — come si
toglie a qualcuno l'accesso ai propri dati, o come se lo toglie da solo:

1. `users.session_epoch`, il numero che finisce nel cookie firmato. Alzarlo
   invalida all'istante tutte le sessioni di quell'utente. Prima non c'era
   niente del genere: un cookie valeva trenta giorni qualunque cosa
   succedesse dopo, cambio password e disattivazione compresi.

2. `ON DELETE` su tutte le chiavi esterne. Fino a qui non ne aveva nessuna,
   quindi cancellare un account non era «sconsigliato»: era impossibile, e su
   Postgres sarebbe fallito sul primo vincolo.

Revision ID: 9c4e1b2f07ad
Revises: 781669583524
Create Date: 2026-09-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "9c4e1b2f07ad"
down_revision = "781669583524"
branch_labels = None
depends_on = None


# (tabella, colonna, tabella referenziata, colonna referenziata, regola)
#
# `CASCADE` per tutto ciò che *è* dati dell'utente. `SET NULL` per i codici
# invito, che sono la traccia di chi ha invitato chi: sopravvivono a entrambi
# e non contengono niente di personale oltre al collegamento.
FOREIGN_KEYS: list[tuple[str, str, str, str, str]] = [
    ("provider_connections", "user_id", "users", "id", "CASCADE"),
    ("push_subscriptions", "user_id", "users", "id", "CASCADE"),
    ("activities", "user_id", "users", "id", "CASCADE"),
    ("sleep_records", "user_id", "users", "id", "CASCADE"),
    ("training_metrics", "user_id", "users", "id", "CASCADE"),
    ("daily_wellness", "user_id", "users", "id", "CASCADE"),
    ("body_composition", "user_id", "users", "id", "CASCADE"),
    ("user_goals", "user_id", "users", "id", "CASCADE"),
    ("training_plans", "user_id", "users", "id", "CASCADE"),
    ("daily_coach_cache", "user_id", "users", "id", "CASCADE"),
    ("gamification_state", "user_id", "users", "id", "CASCADE"),
    ("chat_conversations", "user_id", "users", "id", "CASCADE"),
    ("chat_messages", "conversation_id", "chat_conversations", "id", "CASCADE"),
    ("ai_usage_log", "user_id", "users", "id", "CASCADE"),
    ("notification_log", "user_id", "users", "id", "CASCADE"),
    ("invite_codes", "created_by_id", "users", "id", "SET NULL"),
    ("invite_codes", "used_by_id", "users", "id", "SET NULL"),
    ("training_plans", "goal_id", "user_goals", "id", "SET NULL"),
]


def _fk_name(table: str, column: str, referred: str) -> str | None:
    """Il nome vero del vincolo, chiesto al motore invece che indovinato.

    È la stessa trappola della migrazione `130d59b5783a`, su un'altra famiglia
    di vincoli: SQLite non nomina le chiavi esterne dichiarate dentro la
    tabella e restituisce `None`, Postgres se le battezza da sé
    (`activities_user_id_fkey`). Un nome scritto a mano funziona su un motore
    solo, e indovinate su quale si scopre.
    """
    for fk in sa.inspect(op.get_bind()).get_foreign_keys(table):
        if fk["constrained_columns"] == [column] and fk["referred_table"] == referred:
            return fk["name"]
    return None


def _rewrite_foreign_keys(ondelete: str | None) -> None:
    """Riscrive tutte le chiavi esterne con (o senza) la regola di cancellazione.

    Su SQLite le chiavi esterne vivono dentro il `CREATE TABLE` e non si
    alterano: si rilegge la tabella dal database, si riscrive la regola sui
    vincoli riflessi e `batch_alter_table` ricrea il tutto. Su Postgres si
    lascia cadere il vincolo e lo si riscrive.

    `ondelete=None` toglie le regole: è la strada del downgrade.
    """
    bind = op.get_bind()
    is_sqlite = bind.dialect.name == "sqlite"

    if is_sqlite:
        for name in dict.fromkeys(t for t, *_ in FOREIGN_KEYS):
            # La tabella si **legge dal database**, non dai modelli.
            #
            # Prendere `Base.metadata` sarebbe stato più corto e sarebbe stato
            # un errore: una migrazione descrive un passaggio fra due stati
            # fissi, e se la sua sorgente sono i modelli vivi, il giorno in cui
            # qualcuno aggiunge una colonna questa migrazione prova a copiare
            # una colonna che a quel punto della storia non esiste ancora. Per
            # la cronaca è successo alla prima occasione utile.
            table = sa.Table(name, sa.MetaData(), autoload_with=bind)

            for constraint in table.constraints:
                if not isinstance(constraint, sa.ForeignKeyConstraint):
                    continue
                columns = list(constraint.column_keys)
                for t, column, referred, _referred_col, rule in FOREIGN_KEYS:
                    if t == name and columns == [column]:
                        constraint.ondelete = rule if ondelete is not None else None

            # La riflessione porta con sé anche gli indici, quindi il batch li
            # ricrea da solo: ricrearli a mano qui darebbe «index already
            # exists». (Con `copy_from` preso dai modelli invece sparivano, ed
            # è un'altra ragione per leggere dal database.)
            with op.batch_alter_table(name, copy_from=table, recreate="always"):
                pass
        return

    for table, column, referred, referred_col, rule in FOREIGN_KEYS:
        name = _fk_name(table, column, referred)
        if name is None:
            continue
        op.drop_constraint(name, table, type_="foreignkey")
        op.create_foreign_key(
            name, table, referred, [column], [referred_col],
            ondelete=(rule if ondelete is not None else None),
        )


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(
            sa.Column(
                "session_epoch", sa.Integer(), nullable=False, server_default="0"
            )
        )

    _rewrite_foreign_keys("CASCADE")


def downgrade() -> None:
    _rewrite_foreign_keys(None)

    with op.batch_alter_table("users") as batch:
        batch.drop_column("session_epoch")
