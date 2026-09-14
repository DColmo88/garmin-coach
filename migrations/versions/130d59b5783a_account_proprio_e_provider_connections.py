"""account proprio e provider_connections

Separa **chi sei** da **dove arrivano i tuoi dati**.

Fino a qui le credenziali Garmin erano il login: `garmin_email` era l'identità
e `garmin_password_hash` la prova. Non regge per chi usa Strava — non ha un
account Garmin da inserire — e lega l'accesso a un fornitore che si può
cambiare.

La regola che questa migrazione rispetta sopra ogni altra: **nessuno perde
l'accesso**. L'email di Garmin diventa l'email dell'account e l'hash bcrypt
viene copiato invariato, quindi chi entrava ieri entra oggi con le stesse
identiche credenziali. Il cambio password è un invito, non un obbligo.

Revision ID: 130d59b5783a
Revises: 19dbcc35723a
Create Date: 2026-08-15 11:04:12.551903

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '130d59b5783a'
down_revision: Union[str, None] = '19dbcc35723a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# I vincoli inline non hanno nome, e SQLite e Postgres gliene danno di diversi:
# senza questa convenzione `batch_alter_table` non saprebbe cosa lasciar cadere
# quando ricostruisce la tabella.
CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def _uq_name(table: str, columns: set[str], fallback: str) -> str:
    """Il nome vero del vincolo di unicità, chiesto al motore invece di indovinato.

    Qui SQLite e Postgres non vanno d'accordo, ed è il tipo di differenza che si
    scopre in produzione e non in sviluppo:

    - SQLite non dà un nome ai vincoli dichiarati dentro la tabella, quindi in
      `batch_alter_table` vale la convenzione qui sopra — `uq_activities_user_id`;
    - Postgres se lo inventa da sé concatenando le colonne, e viene fuori
      `activities_user_id_garmin_activity_id_key`.

    Lasciar cadere il nome sbagliato non fallisce a metà: fallisce **solo** sul
    motore che non hai davanti.
    """
    for uc in sa.inspect(op.get_bind()).get_unique_constraints(table):
        if set(uc["column_names"]) == columns:
            return uc["name"] or fallback
    return fallback


def upgrade() -> None:
    # ------------------------------------------------------------------
    # users: l'account diventa suo
    # ------------------------------------------------------------------
    with op.batch_alter_table("users", naming_convention=CONVENTION) as b:
        b.add_column(sa.Column("email", sa.String(), nullable=True))
        b.add_column(sa.Column("password_hash", sa.String(), nullable=True))

    # Il passaggio che salva l'accesso: stessa email, stesso hash bcrypt.
    op.execute("UPDATE users SET email = garmin_email, password_hash = garmin_password_hash")

    # ------------------------------------------------------------------
    # provider_connections: la sorgente esce da users
    # ------------------------------------------------------------------
    op.create_table(
        "provider_connections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("secret_encrypted", sa.String(), nullable=True),
        sa.Column("access_token_encrypted", sa.String(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="ok"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name="fk_provider_connections_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_provider_connections"),
        # Una sorgente sola per utente: Garmin **oppure** Strava.
        sa.UniqueConstraint("user_id", name="uq_provider_connections_user_id"),
    )
    op.create_index("ix_provider_connections_user_id", "provider_connections", ["user_id"])

    # Chi era registrato aveva per definizione Garmin: la connessione esisteva
    # già, era solo scritta dentro `users`.
    op.execute(
        "INSERT INTO provider_connections "
        "  (user_id, provider, external_id, secret_encrypted, status, created_at) "
        "SELECT id, 'garmin', garmin_email, garmin_password_encrypted, 'ok', CURRENT_TIMESTAMP "
        "FROM users"
    )

    # ------------------------------------------------------------------
    # Via le colonne Garmin da users
    # ------------------------------------------------------------------
    with op.batch_alter_table("users", naming_convention=CONVENTION) as b:
        b.drop_index("ix_users_garmin_email")
        b.drop_column("garmin_email")
        b.drop_column("garmin_password_hash")
        b.drop_column("garmin_password_encrypted")
        b.alter_column("email", existing_type=sa.String(), nullable=False)
        b.alter_column("password_hash", existing_type=sa.String(), nullable=False)
        b.create_index("ix_users_email", ["email"], unique=True)

    # ------------------------------------------------------------------
    # activities: l'id non è più «di Garmin»
    # ------------------------------------------------------------------
    with op.batch_alter_table("activities", naming_convention=CONVENTION) as b:
        b.add_column(sa.Column("source", sa.String(), nullable=False,
                               server_default="garmin"))
        b.add_column(sa.Column("external_id", sa.BigInteger(), nullable=True))

    op.execute("UPDATE activities SET external_id = garmin_activity_id, source = 'garmin'")

    old_uq = _uq_name(
        "activities", {"user_id", "garmin_activity_id"}, "uq_activities_user_id"
    )
    with op.batch_alter_table("activities", naming_convention=CONVENTION) as b:
        b.drop_constraint(old_uq, type_="unique")
        b.drop_index("ix_activities_garmin_activity_id")
        b.drop_column("garmin_activity_id")
        b.alter_column("external_id", existing_type=sa.BigInteger(), nullable=False)
        # Il valore di riempimento ha finito il suo lavoro: se restasse, lo
        # schema direbbe una cosa che i modelli non dicono.
        b.alter_column("source", existing_type=sa.String(), server_default=None)
        b.create_index("ix_activities_external_id", ["external_id"])
        b.create_index("ix_activities_source", ["source"])
        b.create_unique_constraint(
            "uq_activities_user_id", ["user_id", "source", "external_id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("activities", naming_convention=CONVENTION) as b:
        b.add_column(sa.Column("garmin_activity_id", sa.BigInteger(), nullable=True))
    op.execute("UPDATE activities SET garmin_activity_id = external_id")
    with op.batch_alter_table("activities", naming_convention=CONVENTION) as b:
        b.drop_constraint("uq_activities_user_id", type_="unique")
        b.drop_index("ix_activities_external_id")
        b.drop_index("ix_activities_source")
        b.drop_column("external_id")
        b.drop_column("source")
        b.alter_column("garmin_activity_id", existing_type=sa.BigInteger(), nullable=False)
        b.create_index("ix_activities_garmin_activity_id", ["garmin_activity_id"])
        b.create_unique_constraint("uq_activities_user_id",
                                   ["user_id", "garmin_activity_id"])

    with op.batch_alter_table("users", naming_convention=CONVENTION) as b:
        b.add_column(sa.Column("garmin_email", sa.String(), nullable=True))
        b.add_column(sa.Column("garmin_password_hash", sa.String(), nullable=True))
        b.add_column(sa.Column("garmin_password_encrypted", sa.String(), nullable=True))

    op.execute(
        "UPDATE users SET garmin_email = email, garmin_password_hash = password_hash, "
        "garmin_password_encrypted = COALESCE("
        "  (SELECT secret_encrypted FROM provider_connections "
        "   WHERE provider_connections.user_id = users.id AND provider = 'garmin'), '')"
    )

    with op.batch_alter_table("users", naming_convention=CONVENTION) as b:
        b.drop_index("ix_users_email")
        b.drop_column("email")
        b.drop_column("password_hash")
        b.alter_column("garmin_email", existing_type=sa.String(), nullable=False)
        b.alter_column("garmin_password_hash", existing_type=sa.String(), nullable=False)
        b.alter_column("garmin_password_encrypted", existing_type=sa.String(), nullable=False)
        b.create_index("ix_users_garmin_email", ["garmin_email"], unique=True)

    op.drop_index("ix_provider_connections_user_id", table_name="provider_connections")
    op.drop_table("provider_connections")
