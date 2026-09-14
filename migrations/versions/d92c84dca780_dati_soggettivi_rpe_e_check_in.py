"""Dati soggettivi: RPE sulle attività e check-in del mattino

L'unica cosa in tutta l'app che non arriva da un apparecchio, e in letteratura
quella che predice l'affaticamento meglio di HRV e frequenza a riposo.

Con loro viaggia `activities.hr_zone_bounds_json`: i battiti in cui ogni zona
comincia, presi dalla stessa risposta che porta i secondi per zona. L'app
mostrava confini calcolati come percentuali della FC massima mentre i secondi
erano stati contati con le zone impostate sull'orologio, che possono essere
tarate su riserva cardiaca o su soglia: due numeri diversi presentati come lo
stesso.

`activities.rpe` sta sulla tabella delle attività e non in una tabella sua
perché `activity_load` lo legge per ogni attività di centottanta giorni: una
join in quel punto si sentirebbe. La sync scrive campo per campo, quindi non
lo sovrascrive mai.

Revision ID: d92c84dca780
Revises: 9c4e1b2f07ad
Create Date: 2026-09-13
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd92c84dca780'
down_revision: Union[str, None] = '9c4e1b2f07ad'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('daily_checkins',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('energy', sa.Integer(), nullable=True),
    sa.Column('legs', sa.Integer(), nullable=True),
    sa.Column('mood', sa.Integer(), nullable=True),
    sa.Column('sleep_quality', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'day')
    )
    with op.batch_alter_table('daily_checkins', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_daily_checkins_day'), ['day'], unique=False)
        batch_op.create_index(batch_op.f('ix_daily_checkins_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('activities', schema=None) as batch_op:
        batch_op.add_column(sa.Column('rpe', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('feel_note', sa.Text(), nullable=True))
        # I confini in battiti con cui Garmin ha contato i secondi per zona.
        batch_op.add_column(sa.Column('hr_zone_bounds_json', sa.JSON(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('activities', schema=None) as batch_op:
        batch_op.drop_column('hr_zone_bounds_json')
        batch_op.drop_column('feel_note')
        batch_op.drop_column('rpe')

    with op.batch_alter_table('daily_checkins', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_daily_checkins_user_id'))
        batch_op.drop_index(batch_op.f('ix_daily_checkins_day'))

    op.drop_table('daily_checkins')
