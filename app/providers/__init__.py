"""Da dove arrivano i dati: Garmin **oppure** Strava.

Fino alla v2 l'app dava per scontato Garmin ovunque. Non era un difetto di
progettazione — c'era un fornitore solo — ma diventa un difetto appena se ne
aggiunge un altro che **misura meno cose**.

Il concetto che tiene insieme questo pacchetto è la **capacità**: ogni
fornitore dichiara cosa sa dare, e l'interfaccia decide di conseguenza. Non è
un dettaglio implementativo, è la regola di prodotto:

    quello che un utente non può avere, per lui non esiste.

Niente pagine vuote, niente «dato non disponibile» ripetuto otto volte al
giorno. Il gruppo Salute semplicemente non compare per chi usa Strava, e la
differenza fra i due fornitori si paga **una volta sola**, nella schermata in
cui si sceglie (`/connect`).

Perché uno solo per volta: Strava importa da Garmin, quindi due connessioni
insieme darebbero la stessa corsa due volte, e una deduplica per sovrapposizione
temporale sbaglierebbe al primo cambio di fuso orario. Il vincolo sta nel
database (`UniqueConstraint("user_id")`), non solo qui.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    BodyComposition,
    DailyWellness,
    ProviderConnection,
    SleepRecord,
    TrainingMetric,
    User,
)

GARMIN = "garmin"
STRAVA = "strava"


class Capability:
    """Cosa un fornitore sa dare. Sono i nomi usati anche nei template."""

    ACTIVITIES = "activities"  # allenamenti: durata, distanza, FC, potenza
    ZONES = "zones"            # secondi per zona di frequenza cardiaca
    SLEEP = "sleep"            # durata e qualità del sonno
    WELLNESS = "wellness"      # FC a riposo, stress, Body Battery, passi
    TRAINING = "training"      # VO2max, training status, HRV
    BODY = "body"              # peso e composizione corporea


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    # Una riga che dice *cosa* si ottiene, non come funziona il collegamento.
    tagline: str
    capabilities: frozenset[str]
    # Come si collega: "password" (Garmin) oppure "oauth" (Strava).
    connect_style: str


GARMIN_PROVIDER = Provider(
    key=GARMIN,
    label="Garmin",
    tagline="Tutto quello che l'app sa fare",
    capabilities=frozenset({
        Capability.ACTIVITIES, Capability.ZONES, Capability.SLEEP,
        Capability.WELLNESS, Capability.TRAINING, Capability.BODY,
    }),
    connect_style="password",
)

STRAVA_PROVIDER = Provider(
    key=STRAVA,
    label="Strava",
    tagline="Solo gli allenamenti",
    # Strava è una piattaforma di attività, non un dispositivo: non misura
    # niente di notte. Non è una limitazione dell'API, è cosa Strava *è*.
    capabilities=frozenset({Capability.ACTIVITIES, Capability.ZONES}),
    connect_style="oauth",
)

PROVIDERS: dict[str, Provider] = {p.key: p for p in (GARMIN_PROVIDER, STRAVA_PROVIDER)}


# ============================================================================
# Le sezioni dell'interfaccia, e da quale capacità dipendono
# ============================================================================

# Chiave = nome della sezione nei template e nelle rotte; valore = la capacità
# senza la quale quella sezione non ha niente da dire.
SECTION_REQUIRES: dict[str, str] = {
    "sleep": Capability.SLEEP,
    "health": Capability.WELLNESS,
    "body": Capability.BODY,
    # La prontezza pesa il sonno per 0,22 e l'HRV per 0,18: con una sorgente
    # che non misura di notte, quei due non arriveranno mai. Non basta però a
    # escluderla, perché soggettivo, carico e forma insieme superano la soglia
    # di fondatezza: vedi `readiness_available`.
    "readiness": Capability.SLEEP,
}

# Dove guardare per sapere se di una sezione esiste già dello storico.
_HISTORY_MODELS = {
    "sleep": SleepRecord,
    "health": DailyWellness,
    "body": BodyComposition,
    "readiness": TrainingMetric,
}


def provider_of(user: User) -> Provider | None:
    """Il fornitore collegato, o None se l'utente non ne ha ancora scelto uno."""
    conn = getattr(user, "connection", None)
    return PROVIDERS.get(conn.provider) if conn is not None else None


def capabilities(user: User) -> frozenset[str]:
    """Cosa si può alimentare per questo utente. Vuoto se non ha collegato niente."""
    provider = provider_of(user)
    return provider.capabilities if provider else frozenset()


def has(user: User, capability: str) -> bool:
    return capability in capabilities(user)


def sections_with_history(db: Session, user: User) -> set[str]:
    """Le sezioni di cui esiste già almeno una riga.

    Serve a chi **cambia** fornitore: sei mesi di sonno registrati con Garmin
    non devono sparire dal menu perché oggi si usa Strava. Restano visibili e
    smettono di aggiornarsi — ed è l'unico punto dell'app dove si scrive una
    riga di spiegazione, perché lì è un'informazione vera e non una scusa.
    """
    # Una sola andata e ritorno: questo finisce nel menu, quindi gira a **ogni**
    # pagina. Quattro query separate per disegnare una barra laterale sarebbero
    # quattro di troppo.
    sections = list(_HISTORY_MODELS)
    row = db.execute(
        select(*[
            exists().where(_HISTORY_MODELS[s].user_id == user.id).label(s)
            for s in sections
        ])
    ).one()
    return {section for section, present in zip(sections, row) if present}


# Quanti check-in recenti bastano perché la prontezza abbia senso di esistere
# anche senza sensori notturni. Tre su sette: uno solo è un tentativo, tre sono
# un'abitudine, e sotto quella soglia la pagina mostrerebbe «dati insufficienti»
# quasi tutti i giorni — che è la pagina vuota con la scusa, cioè quello che
# questo modulo serve a non fare.
CHECKIN_DAYS_FOR_READINESS = 3


def readiness_available(db: Session, user: User) -> bool:
    """Se la prontezza ha abbastanza materiale per questo atleta.

    La regola di prodotto resta «quello che un utente non può avere, per lui
    non esiste», ma cosa può avere è cambiato: da quando c'è il check-in del
    mattino, soggettivo + carico + forma superano la soglia di fondatezza
    anche per chi non misura niente di notte. Per un utente Strava la prontezza
    non è più impossibile: è condizionata a rispondere a tre domande.

    Chi il check-in non lo fa resta com'era, e su `/coach` vede la forma al
    posto della prontezza.
    """
    from datetime import timedelta

    from app.clock import today_for
    from app.db.models import DailyCheckin

    if Capability.SLEEP in capabilities(user):
        return True

    today = today_for(user)
    recent = db.scalar(
        select(func.count(DailyCheckin.id)).where(
            DailyCheckin.user_id == user.id,
            DailyCheckin.day > today - timedelta(days=7),
        )
    ) or 0
    return recent >= CHECKIN_DAYS_FOR_READINESS


def visible_sections(db: Session, user: User) -> frozenset[str]:
    """Cosa mostrare: quello che il fornitore alimenta, più quello che ha storia."""
    live = {s for s, req in SECTION_REQUIRES.items() if req in capabilities(user)}
    if readiness_available(db, user):
        live.add("readiness")
    return frozenset(live | sections_with_history(db, user))


def is_live(user: User, section: str) -> bool:
    """True se la sezione si aggiorna ancora, False se è solo storia congelata."""
    required = SECTION_REQUIRES.get(section)
    return required is None or required in capabilities(user)


# ============================================================================
# Sincronizzazione
# ============================================================================

# Gli import dei due moduli concreti stanno dentro la funzione: `garmin.py`
# importa i modelli e `strava.py` importa stravalib, e caricarli qui in cima
# creerebbe un ciclo con chi importa questo pacchetto per le sole capacità.
def _sync_function(provider_key: str) -> Callable[[Session, User, ProviderConnection], dict[str, int]]:
    if provider_key == GARMIN:
        from app.providers.garmin import sync as garmin_sync

        return garmin_sync
    if provider_key == STRAVA:
        from app.providers.strava import sync as strava_sync

        return strava_sync
    raise ValueError(f"fornitore sconosciuto: {provider_key}")


def sync_user(db: Session, user: User) -> dict[str, int]:
    """Sincronizza dal fornitore collegato. Nessuna connessione, nessun lavoro.

    Non solleva se l'utente non ha collegato niente: è uno stato normale fra la
    registrazione e il primo collegamento, non un errore.
    """
    conn = getattr(user, "connection", None)
    if conn is None:
        return {}
    return _sync_function(conn.provider)(db, user, conn)
