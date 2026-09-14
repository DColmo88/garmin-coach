"""Strava come fornitore, via `stravalib`.

Cosa dà e cosa no
-----------------
Strava è una piattaforma di **attività**, non un dispositivo: registra quello
che l'orologio ha misurato durante l'allenamento e niente altro. Non esiste un
endpoint per il sonno, l'HRV o la frequenza a riposo — non è una restrizione
dell'API, è che Strava quei dati non li raccoglie proprio.

Per l'app significa che restano interi il carico, le curve CTL/ATL/TSB, le zone
e i primati — tutta roba che `app/analysis/` calcola da sé partendo da durata,
frequenza e potenza — mentre il gruppo Salute non ha nulla da mostrare. Le
capacità dichiarate in `app/providers/__init__.py` sono il modo in cui questo
fatto arriva all'interfaccia, che nasconde quelle sezioni invece di riempirle
di scuse.

I token
-------
L'access token vive **sei ore**; quello che dura è il refresh token. E Strava
lo **ruota** a ogni rinnovo: se non si salva quello nuovo, il collegamento si
rompe silenziosamente al rinnovo successivo. Per questo `_fresh_token` scrive
sempre su database prima di restituire.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import decrypt_secret, encrypt_secret
from app.config import settings
from app.db.models import Activity, ProviderConnection, User

logger = logging.getLogger(__name__)


class StravaError(Exception):
    """Strava ha rifiutato la richiesta, o l'app non è configurata."""


# Il minimo che serve per leggere le attività dell'atleta, comprese quelle che
# ha marcato come private: chiedere di più sarebbe chiedere per abitudine.
SCOPE = ["activity:read_all"]

# Le zone sono una chiamata per attività, come per Garmin: stesso tetto, così
# la prima sincronizzazione non brucia il rate limit in un colpo solo.
ZONES_PER_SYNC = 25

# Quanto indietro andare la prima volta.
FIRST_SYNC_DAYS = 365

# Riscarica anche qualche giorno già visto: capita di correggere un'attività su
# Strava dopo averla caricata, e la finestra la ripesca senza duplicare nulla.
OVERLAP_DAYS = 3


# ============================================================================
# OAuth
# ============================================================================

def _require_config() -> tuple[int, str]:
    if not settings.strava_configured:
        raise StravaError(
            "Strava non è configurato su questo server: mancano STRAVA_CLIENT_ID "
            "e STRAVA_CLIENT_SECRET."
        )
    return int(settings.STRAVA_CLIENT_ID), settings.STRAVA_CLIENT_SECRET


def authorization_url(redirect_uri: str, state: str) -> str:
    """L'indirizzo a cui mandare l'utente per autorizzare l'app."""
    from stravalib import Client

    client_id, _ = _require_config()
    return Client().authorization_url(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=SCOPE,
        # `force` perché altrimenti chi ha già autorizzato una volta viene
        # rimbalzato indietro senza vedere cosa sta concedendo.
        approval_prompt="auto",
        state=state,
    )


def connect(db: Session, user: User, code: str) -> ProviderConnection:
    """Scambia il codice temporaneo con i token e salva la connessione."""
    from stravalib import Client

    client_id, client_secret = _require_config()

    try:
        result = Client().exchange_code_for_token(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            return_athlete=True,
        )
    except Exception as exc:  # noqa: BLE001 — la libreria solleva di tutto
        # Il testo di `exc` finiva nell'indirizzo di ritorno e quindi a schermo:
        # stravalib ci mette dentro l'URL chiamato e la risposta, che può
        # contenere il `client_secret` quando l'errore è di configurazione.
        logger.warning("Scambio del codice Strava fallito: %s", exc)
        raise StravaError(
            "Strava ha rifiutato l'autorizzazione. Riprova a collegarlo."
        ) from exc

    token, athlete = result if isinstance(result, tuple) else (result, None)

    conn = user.connection or ProviderConnection(user_id=user.id)
    conn.provider = "strava"
    conn.external_id = str(getattr(athlete, "id", "") or "") or None
    conn.secret_encrypted = encrypt_secret(token["refresh_token"])
    conn.access_token_encrypted = encrypt_secret(token["access_token"])
    conn.token_expires_at = _from_epoch(token["expires_at"])
    conn.status = "ok"

    if conn.id is None:
        db.add(conn)
    db.commit()
    db.refresh(user)
    return conn


def disconnect(conn: ProviderConnection) -> None:
    """Revoca l'autorizzazione presso Strava.

    Se fallisce non è grave e non deve bloccare niente: il collegamento locale
    sparisce comunque, e l'utente può sempre togliere l'app dalle sue
    impostazioni Strava.
    """
    try:
        from stravalib import Client

        Client(access_token=decrypt_secret(conn.access_token_encrypted)).deauthorize()
    except Exception as exc:  # noqa: BLE001
        logger.info("Revoca Strava non riuscita (non è bloccante): %s", exc)


def _from_epoch(value) -> datetime | None:
    try:
        return datetime.utcfromtimestamp(int(value))
    except (TypeError, ValueError):
        return None


def _fresh_token(db: Session, conn: ProviderConnection) -> str:
    """Un access token valido, rinnovandolo se serve.

    Il refresh token **ruota**: quello vecchio smette di valere appena Strava
    ne emette uno nuovo. Va scritto su database prima di usare l'access token,
    non dopo, altrimenti un errore nel mezzo lascia il collegamento morto.
    """
    from stravalib import Client

    margin = datetime.utcnow() + timedelta(minutes=5)
    if conn.access_token_encrypted and conn.token_expires_at and conn.token_expires_at > margin:
        return decrypt_secret(conn.access_token_encrypted)

    client_id, client_secret = _require_config()
    try:
        token = Client().refresh_access_token(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=decrypt_secret(conn.secret_encrypted),
        )
    except Exception as exc:  # noqa: BLE001
        conn.status = "auth_failed"
        db.commit()
        raise StravaError(
            "Strava non ha rinnovato l'accesso: probabilmente hai revocato "
            "l'autorizzazione. Ricollegalo da /connect."
        ) from exc

    conn.secret_encrypted = encrypt_secret(token["refresh_token"])
    conn.access_token_encrypted = encrypt_secret(token["access_token"])
    conn.token_expires_at = _from_epoch(token["expires_at"])
    conn.status = "ok"
    db.commit()
    return token["access_token"]


def _client(db: Session, conn: ProviderConnection):
    from stravalib import Client

    # `rate_limit_requests` è già True di suo: stravalib rispetta da solo i
    # 100 letture / 15 minuti, quindi non serve un limitatore nostro.
    return Client(access_token=_fresh_token(db, conn))


# ============================================================================
# Dal vocabolario di Strava al nostro
# ============================================================================

_CAMEL = re.compile(r"(?<!^)(?=[A-Z])")


def normalise_sport(sport_type: str | None) -> str | None:
    """`MountainBikeRide` → `mountain_bike_ride`.

    Strava nomina gli sport in CamelCase, Garmin in snake_case. `app/sports.py`
    cerca per sottostringa su minuscolo, quindi senza questo passaggio
    `TrailRun` non incontrerebbe mai `trail_run` e ogni attività Strava
    finirebbe in «Altro» con l'icona sbagliata.
    """
    if not sport_type:
        return None
    return _CAMEL.sub("_", str(sport_type)).lower()


def _num(value) -> float | None:
    """Un float, qualunque cosa `stravalib` abbia deciso di restituire.

    Fra le versioni della libreria gli stessi campi sono stati float grezzi,
    `Quantity` di Pint e `timedelta`. Qui si normalizza una volta invece di
    scoprirlo a valle.
    """
    if value is None:
        return None
    if isinstance(value, timedelta):
        return value.total_seconds()
    for attr in ("magnitude", "num"):  # Pint, e le vecchie unità di stravalib
        if hasattr(value, attr):
            value = getattr(value, attr)
            break
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _naive(value) -> datetime | None:
    """Datetime senza fuso: nel database le date stanno tutte così."""
    if not isinstance(value, datetime):
        return None
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


# ============================================================================
# Sincronizzazione
# ============================================================================

def sync_activities(db: Session, user: User, conn: ProviderConnection) -> int:
    client = _client(db, conn)

    latest = db.scalar(
        select(Activity.start_time)
        .where(Activity.user_id == user.id, Activity.source == "strava")
        .order_by(Activity.start_time.desc())
        .limit(1)
    )
    after = (
        latest - timedelta(days=OVERLAP_DAYS)
        if latest
        else datetime.utcnow() - timedelta(days=FIRST_SYNC_DAYS)
    )

    count = 0
    for act in client.get_activities(after=after):
        sid = getattr(act, "id", None)
        if sid is None:
            continue

        existing = db.scalar(
            select(Activity).where(
                Activity.user_id == user.id,
                Activity.source == "strava",
                Activity.external_id == sid,
            )
        )
        row = existing or Activity(user_id=user.id, source="strava", external_id=sid)

        row.name = getattr(act, "name", None)
        row.activity_type = normalise_sport(
            getattr(act, "sport_type", None) or getattr(act, "type", None)
        )
        row.start_time = _naive(getattr(act, "start_date_local", None))
        # `moving_time` invece di `elapsed_time`: le soste al semaforo non sono
        # allenamento, e il carico si calcola sul tempo in movimento.
        row.duration_sec = _num(getattr(act, "moving_time", None))
        row.distance_m = _num(getattr(act, "distance", None))
        row.avg_hr = _num(getattr(act, "average_heartrate", None))
        row.max_hr = _num(getattr(act, "max_heartrate", None))
        row.avg_speed = _num(getattr(act, "average_speed", None))
        row.calories = _num(getattr(act, "calories", None)) or _num(
            getattr(act, "kilojoules", None)
        )
        row.elevation_gain_m = _num(getattr(act, "total_elevation_gain", None))
        row.avg_cadence = _num(getattr(act, "average_cadence", None))
        row.avg_power = _num(getattr(act, "average_watts", None))
        # Training effect è una metrica Garmin: Strava non ce l'ha, e lasciarla
        # a None è più onesto che stimarla.

        if existing is None:
            db.add(row)
        count += 1

    db.commit()
    logger.info("Strava: sincronizzate %d attività (utente %s).", count, user.id)
    return count


def sync_activity_zones(
    db: Session, user: User, conn: ProviderConnection, limit: int = ZONES_PER_SYNC
) -> int:
    """Secondi per zona di frequenza cardiaca, una chiamata per attività."""
    pending = db.scalars(
        select(Activity)
        .where(
            Activity.user_id == user.id,
            Activity.source == "strava",
            Activity.hr_zones_json.is_(None),
            Activity.avg_hr.is_not(None),
        )
        .order_by(Activity.start_time.desc())
        .limit(limit)
    ).all()
    if not pending:
        return 0

    client = _client(db, conn)
    count = 0
    for activity in pending:
        try:
            zones = client.get_activity_zones(activity.external_id)
        except Exception as exc:  # noqa: BLE001
            # Capita: attività senza dati di zona, o senza abbonamento Strava.
            # Non è un errore da propagare — la sync deve proseguire.
            logger.debug("Zone Strava non disponibili per %s: %s", activity.external_id, exc)
            continue

        buckets = _heartrate_buckets(zones)
        if buckets:
            activity.hr_zones_json = buckets
            count += 1

    db.commit()
    logger.info("Strava: zone scaricate per %d attività (utente %s).", count, user.id)
    return count


def _heartrate_buckets(zones) -> list[float] | None:
    """I secondi per zona dalla risposta di Strava, o None se non ce ne sono."""
    for zone in zones or []:
        if getattr(zone, "type", None) != "heartrate":
            continue
        seconds = [
            _num(getattr(b, "time", None)) or 0.0
            for b in getattr(zone, "distribution_buckets", None) or []
        ]
        if any(seconds):
            return seconds
    return None


def sync(db: Session, user: User, conn: ProviderConnection) -> dict[str, int]:
    return {
        "activities": sync_activities(db, user, conn),
        "zones": sync_activity_zones(db, user, conn),
    }
