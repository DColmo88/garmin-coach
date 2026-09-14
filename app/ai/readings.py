"""Le letture in cima a Sonno, Recupero, Corpo e Forma, scritte dall'AI.

La divisione del lavoro è la stessa di tutto il resto dell'app, solo applicata
a un punto in cui prima non lo era:

- **Python decide il tono** (verde, ambra, rosso) e calcola le evidenze. È la
  parte che non deve poter cambiare: se le soglie dicono che il carico è troppo
  alto, nessun modello può decidere che va bene.
- **L'AI scrive le parole.** Verdetto e azione, con davanti i numeri già fatti.

Prima erano ventotto frasi fisse scelte da soglie. Il difetto non era la
qualità delle singole frasi — erano scritte con cura — ma il fatto che dopo una
settimana le avevi lette tutte, e che nessuna poteva tenere conto del resto:
la pagina del sonno non sapeva niente del carico.

Una chiamata sola per tutte e quattro le sezioni, dentro il giro giornaliero.
"""
from __future__ import annotations

import logging

from app.insights.domains import PageReading

logger = logging.getLogger(__name__)

# Il nome italiano che usa il prompt ↔ la chiave con cui l'app conosce la pagina.
PAGES: dict[str, str] = {
    "sonno": "sleep",
    "recupero": "health",
    "corpo": "body",
    "forma": "fitness",
}


def deterministic_readings(db, user) -> dict[str, PageReading]:
    """Le quattro letture calcolate in Python, chiave per chiave.

    Sono sia la base del prompt — tono ed evidenze — sia la riserva da mostrare
    se l'AI non risponde.
    """
    from app import queries as q
    from app.analysis import cache as analysis_cache
    from app.insights import read_body, read_fitness, read_health, read_sleep

    return {
        "sleep": read_sleep(q.sleep_series(db, user.id, 28)),
        "health": read_health(q.wellness_series(db, user.id, 28)),
        "body": read_body(q.body_series(db, user.id, 90)),
        "fitness": read_fitness(q.training_series(db, user.id, 28),
                                analysis_cache.load_summary(db, user)),
    }


def generate(db, user, briefing: str) -> dict[str, dict[str, str]] | None:
    """Verdetto e azione per ogni pagina, o `None` se l'AI non ha risposto."""
    from app.ai.prompts import READINGS_SCHEMA, READINGS_SYSTEM, readings_user_prompt
    from app.ai.provider import get_provider

    base = deterministic_readings(db, user)
    # Le sezioni senza misurazioni non si mandano proprio: non c'è niente da
    # dire meglio, e il modello riempirebbe con «monitora il peso», che è
    # esattamente la frase inutile che la versione scritta a mano evita.
    sections = [
        (name, base[key].tone, base[key].evidence)
        for name, key in PAGES.items()
        if base[key].has_content and base[key].enough_data
    ]
    if not sections:
        return None

    try:
        provider = get_provider()
    except Exception:  # noqa: BLE001 — nessuna chiave configurata
        return None

    # Modello grande per la stessa ragione degli insight: leggere quattro
    # sezioni insieme senza confonderle è esattamente ciò in cui il modello
    # leggero sbaglia.
    answer = provider.analyse(
        READINGS_SYSTEM, readings_user_prompt(briefing, sections), READINGS_SCHEMA,
        heavy=True,
    )
    if not isinstance(answer, dict):
        return None

    out: dict[str, dict[str, str]] = {}
    for name, key in PAGES.items():
        item = answer.get(name)
        if not isinstance(item, dict):
            continue
        verdict = (item.get("verdetto") or "").strip()
        action = (item.get("azione") or "").strip()
        # Il prompt dice di lasciare vuoto quando non c'è abbastanza per dire
        # qualcosa di sensato: rispettarlo significa tenere la frase di riserva
        # invece di mettere in pagina un titolo vuoto.
        if verdict:
            out[key] = {"verdict": verdict, "action": action}
    return out or None


def apply(reading: PageReading, cached: dict | None) -> PageReading:
    """La lettura deterministica con le parole dell'AI, se ce ne sono.

    Tono, evidenze e note restano quelle calcolate: l'AI sostituisce il titolo
    e il consiglio, non il giudizio.
    """
    if not cached or not cached.get("verdict") or not reading.enough_data:
        return reading
    return PageReading(
        verdict=cached["verdict"],
        evidence=reading.evidence,
        action=cached.get("action") or reading.action,
        tone=reading.tone,
        notes=reading.notes,
        enough_data=reading.enough_data,
    )
