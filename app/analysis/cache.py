"""Il quadro del carico, calcolato una volta per richiesta.

`summary.build` legge centottanta giorni di attività, risolve il profilo
fisiologico — che a sua volta cerca il picco di FC su un anno e la mediana
della FC a riposo su due mesi — e srotola una serie PMC di centottanta punti.
Non è costoso in assoluto; è costoso **quattro volte**, che è quante ne
girava per ogni apertura di `/coach`:

    q.coach_snapshot          → _training_load → summary.build
    refresh_daily_cache       → q.coach_snapshot di nuovo → summary.build
    briefing.build            → summary.build
    readings.deterministic_*  → summary.build

Nessuno di quei quattro sapeva degli altri tre, ed è giusto così: sono
componenti che devono poter essere chiamati da soli. La soluzione non è farli
parlare fra loro ma metterci sotto una memoria brevissima, che dura quanto la
richiesta e poi sparisce.

**Quanto dura.** Un `ContextVar` azzerato dal middleware a ogni richiesta HTTP.
Non una cache a tempo: un carico calcolato dieci minuti fa è un carico
sbagliato dopo una sincronizzazione, e inseguire l'invalidazione fra sync,
pipeline notturna e pagine sarebbe più codice di quanto se ne risparmi. Dentro
una richiesta, invece, i dati per definizione non cambiano.

Fuori da una richiesta HTTP — scheduler, CLI, test — non c'è nessuno che
azzeri, quindi la cache resta spenta e ogni chiamata calcola davvero.
"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import User

# `None` vuol dire «non siamo dentro una richiesta»: si calcola e basta.
_cache: ContextVar[dict[Any, Any] | None] = ContextVar("analysis_cache", default=None)


def begin_request() -> None:
    """Apre una memoria vuota per la richiesta corrente."""
    _cache.set({})


def end_request() -> None:
    """Chiude la memoria. Da qui in poi si torna a calcolare ogni volta."""
    _cache.set(None)


def _memo(key: Any, produce):
    store = _cache.get()
    if store is None:
        return produce()
    if key not in store:
        store[key] = produce()
    return store[key]


def load_summary(db: Session, user: User, today: date | None = None):
    """`app.analysis.summary.build`, ma una volta sola per richiesta."""
    from app.analysis import summary
    from app.clock import today_for

    day = today or today_for(user)
    return _memo(
        ("load", user.id, day), lambda: summary.build(db, user, day)
    )


def profile(db: Session, user: User, today: date | None = None):
    """`app.analysis.profile.resolve_profile`, una volta sola per richiesta."""
    from app.analysis.profile import resolve_profile
    from app.clock import today_for

    day = today or today_for(user)
    return _memo(
        ("profile", user.id, day), lambda: resolve_profile(db, user, day)
    )
