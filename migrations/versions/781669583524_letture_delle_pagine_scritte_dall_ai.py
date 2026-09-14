"""letture delle pagine scritte dall'AI

Una colonna sola: `daily_coach_cache.readings_json`, dove finiscono verdetto e
azione che il modello scrive per Sonno, Recupero, Corpo e Forma.

Sta qui e non in una tabella sua perché ha esattamente la stessa vita del resto
della riga — un giorno, un utente, una chiamata al modello — e una tabella in
più per quattro stringhe sarebbe una join in più a ogni pagina.

Revision ID: 781669583524
Revises: 130d59b5783a
Create Date: 2026-08-16 09:12:44.180255

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '781669583524'
down_revision: Union[str, None] = '130d59b5783a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: le righe di ieri non hanno letture e non devono averne.
    op.add_column(
        "daily_coach_cache",
        sa.Column("readings_json", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("daily_coach_cache", "readings_json")
