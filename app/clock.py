"""Che giorno è, per questa persona.

`date.today()` risponde col fuso del server. Sembra un dettaglio e non lo è:
qui il «giorno» decide la chiave della cache del coaching, l'azzeramento delle
quote AI, le serie del briefing, il cooldown delle notifiche, la finestra del
rapporto acuto/cronico e la serie della gamification. Con il server su UTC e
l'atleta a Roma, per due ore ogni notte l'app parlava di ieri — e chi apriva
l'app all'una si vedeva il coaching del giorno prima.

`User.timezone` esisteva dalla v2 e non lo leggeva nessuno. Adesso passa da
qui, e il fuso non compare da nessun'altra parte.

Sugli orari: `now_utc()` restituisce un `datetime` **consapevole del fuso**.
I `datetime` salvati sul database restano ingenui per non dover riscrivere
sette anni di righe, quindi `naive_utc()` esiste per quello: è esplicito su
cosa sta facendo, che è meglio di `datetime.utcnow()` sparso — deprecata, e
ambigua proprio su questo punto.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Rome"


def now_utc() -> datetime:
    """L'istante corrente, con il fuso dichiarato."""
    return datetime.now(timezone.utc)


def naive_utc() -> datetime:
    """L'istante corrente in UTC, senza fuso: il formato che il database usa."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def zone_of(user) -> ZoneInfo:
    """Il fuso dell'utente, con ricaduta sul default se è scritto male.

    Un fuso inesistente in `users.timezone` non deve poter far fallire il
    calcolo di che giorno è: sarebbe una pagina bianca per un campo di
    configurazione.
    """
    name = getattr(user, "timezone", None) or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def today_for(user) -> date:
    """Che giorno è dove si trova questa persona."""
    return datetime.now(zone_of(user)).date()


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """Inizio e fine di un giorno, per confrontarli con `Activity.start_time`.

    **Nessuna conversione di fuso, ed è voluto.** Entrambi i fornitori salvano
    l'ora *locale dell'allenamento* — `startTimeLocal` per Garmin,
    `start_date_local` per Strava — quindi `start_time` è già l'ora del
    cartello stradale dove la corsa è avvenuta, e `start_time.date()` è già il
    giorno giusto. Convertire da e verso UTC qui sposterebbe di qualche ora le
    uscite serali, cioè introdurrebbe il bug che sembra evitare.

    La funzione esiste lo stesso perché la conversione sbagliata è la cosa che
    viene in mente per prima: averla scritta qui una volta, col motivo accanto,
    è più efficace di sperare che nessuno ci riprovi.
    """
    return (
        datetime.combine(day, datetime.min.time()),
        datetime.combine(day, datetime.max.time()),
    )
