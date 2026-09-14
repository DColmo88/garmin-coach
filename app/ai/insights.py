"""Insight rules engine — Python puro, zero AI, zero latenza.

Ogni insight ha un colore (verde, ambra, rosso) che ne è anche la priorità.
Il colore basta: la scheda lo mostra come barra laterale. Le emoji che c'erano
prima davanti a ogni titolo — 🔋, 📻, 🌙 — non aggiungevano informazione, e
un'interfaccia di dati con un pittogramma davanti a ogni frase sembra un
messaggio di chat.
"""
from __future__ import annotations

from app.clock import today_for

from dataclasses import dataclass

_PRIORITY = {"red": 0, "amber": 1, "green": 2}


@dataclass
class Insight:
    title: str
    text: str
    color: str
    priority: int


def _mk(title: str, text: str, color: str) -> Insight:
    return Insight(title, text, color, _PRIORITY[color])


def _num(value: float, digits: int = 1) -> str:
    """Decimali con la virgola: il testo lo legge una persona italiana."""
    return f"{value:.{digits}f}".replace(".", ",")


def detect_insights(snap: dict) -> list[Insight]:
    out: list[Insight] = []

    rhr7, rhr30 = snap.get("resting_hr_7d_avg"), snap.get("resting_hr_30d_avg")
    if rhr7 is not None and rhr30 is not None:
        if rhr7 <= rhr30 - 3:
            out.append(_mk("FC riposo in calo",
                           f"FC a riposo {round(rhr30 - rhr7)} bpm più bassa del mese scorso "
                           "— fitness in crescita.", "green"))
        elif rhr7 >= rhr30 + 4:
            out.append(_mk("FC riposo in salita",
                           "FC a riposo più alta del solito — possibile stanchezza o stress.",
                           "amber"))

    ratio = snap.get("load_ratio")
    if ratio is not None and ratio >= 1.4:
        out.append(_mk("Carico in salita rapida",
                       f"Questa settimana hai caricato {_num(ratio, 1)} volte quello a cui sei "
                       "abituato. Sopra 1,5 gli infortuni diventano probabili: uno o due "
                       "giorni facili adesso valgono più di una settimana persa dopo.", "red"))

    tsb = snap.get("tsb")
    if tsb is not None:
        if tsb <= -30:
            out.append(_mk("Fatica oltre la fitness",
                           f"La forma è a {round(tsb)}: la fatica accumulata ha superato "
                           "quello che hai costruito. Serve una settimana di scarico, non "
                           "una sessione in meno.", "red"))
        elif tsb >= 25:
            out.append(_mk("Scarico riuscito",
                           f"Forma a +{round(tsb)}: sei fresco. È il momento di una gara o "
                           "di una sessione chiave — restare così a lungo, però, significa "
                           "perdere il lavoro fatto.", "green"))

    grey = snap.get("grey_time_pct")
    if grey is not None and grey >= 35:
        out.append(_mk("Troppo tempo in zona media",
                       f"Il {round(grey)}% del tempo di allenamento sta in zona 3: costa "
                       "quanto una seduta dura senza darne lo stimolo. Rallenta il facile "
                       "e alza il duro.", "amber"))

    mono = snap.get("monotony")
    if mono is not None and mono >= 2.0:
        out.append(_mk("Settimana tutta uguale",
                       "Tutti i giorni con lo stesso carico. È il modo più efficace di "
                       "accumulare fatica senza accumulare adattamento: servono giorni "
                       "davvero duri e giorni davvero vuoti.", "amber"))

    ss7 = snap.get("sleep_score_7d_avg")
    if ss7 is not None and ss7 < 65:
        out.append(_mk("Sonno sotto la media",
                       "Qualità del sonno bassa questa settimana — priorità al recupero.",
                       "amber"))

    vo2_now, vo2_old = snap.get("vo2max_latest"), snap.get("vo2max_4w_ago")
    if vo2_now is not None and vo2_old is not None and vo2_now > vo2_old:
        out.append(_mk("VO₂max in crescita",
                       f"VO₂max +{round(vo2_now - vo2_old, 1)} rispetto a un mese fa "
                       "— sei più forte.", "green"))

    dslr = snap.get("days_since_last_run")
    if dslr is not None and dslr > 5:
        out.append(_mk("Corpo riposato",
                       f"{dslr} giorni senza corsa — il corpo è riposato, momento perfetto "
                       "per ripartire.", "green"))

    streak = snap.get("consecutive_active_days") or 0
    if streak >= 7:
        out.append(_mk("In serie",
                       f"{streak} giorni consecutivi attivi — continua così!", "green"))

    deep = snap.get("deep_pct")
    if deep is not None and deep < 0.15:
        out.append(_mk("Sonno profondo basso",
                       "Sonno profondo sotto il 15% — prova un rituale serale più regolare.",
                       "amber"))

    bb_low = snap.get("body_battery_low_7d_avg")
    if bb_low is not None and bb_low < 20:
        out.append(_mk("Energia a terra",
                       "Body Battery quasi a zero ogni sera — forse stai facendo troppo.",
                       "amber"))

    return out


def top_insights(snap: dict, n: int = 3) -> list[Insight]:
    ordered = sorted(detect_insights(snap), key=lambda i: i.priority)
    return ordered[:n]


# ============================================================================
# La versione scritta dall'AI
# ============================================================================
#
# Le regole qui sopra restano, ma come **rete di sicurezza**: coprono il caso
# senza chiave AI, quota esaurita o risposta illeggibile. Quando l'AI c'è, gli
# insight li scrive lei — perché tredici frasi fisse, per quanto ben scelte, in
# una settimana le hai lette tutte, e nessuna lega mai due cose fra loro.
#
# Quello che l'AI **non** fa è calcolare: medie, delta e confronti arrivano già
# fatti dal briefing (`app.ai.briefing.relation_lines`). Così non può sbagliare
# un numero, solo scegliere male cosa dirne.

MAX_AI_INSIGHTS = 3
_VALID_COLORS = {"green", "amber", "red"}


def _clean_ai_insight(raw: dict) -> Insight | None:
    """Una voce della risposta del modello, se è utilizzabile."""
    if not isinstance(raw, dict):
        return None
    title = (raw.get("title") or "").strip()
    text = (raw.get("text") or "").strip()
    if not title or not text:
        return None
    color = raw.get("color") if raw.get("color") in _VALID_COLORS else "amber"
    return _mk(title, text, color)


def generate_insights(db, user, briefing: str | None = None) -> tuple[list[Insight], str]:
    """Gli insight del giorno e da dove vengono: `("ai" | "deterministic")`.

    Il chiamante ha bisogno di sapere l'origine, non per curiosità ma perché
    decide se la riga di cache può essere riusata domani senza richiamare il
    modello.
    """
    from datetime import date

    from app import queries as q
    from app.ai.prompts import INSIGHTS_SCHEMA, INSIGHTS_SYSTEM, insights_user_prompt
    from app.ai.provider import get_provider

    snap = q.coach_snapshot(db, user.id)
    fallback = top_insights(snap, MAX_AI_INSIGHTS)

    try:
        provider = get_provider()
    except Exception:  # noqa: BLE001 — nessuna chiave configurata
        return fallback, "deterministic"

    if briefing is None:
        from app.ai import briefing as briefing_builder

        briefing = briefing_builder.build(db, user)

    # Modello grande, non quello leggero. Non è una questione di stile: sui
    # confronti settimana-su-settimana `gpt-4o-mini` ha letto al contrario un
    # aumento del sonno e ha scritto «in calo». Un insight sbagliato è peggio
    # di nessun insight, e qui si parla di **una chiamata al giorno**: non è il
    # posto dove si risparmia.
    answer = provider.analyse(
        INSIGHTS_SYSTEM,
        insights_user_prompt(briefing, today_for(user).strftime("%d/%m/%Y")),
        INSIGHTS_SCHEMA,
        heavy=True,
    )
    if answer is None:
        return fallback, "deterministic"

    items = answer.get("insights")
    if not isinstance(items, list):
        return fallback, "deterministic"

    cleaned = [i for i in (_clean_ai_insight(r) for r in items) if i]

    # Un elenco vuoto è una risposta valida, non un guasto: il prompt dice
    # espressamente che certi giorni non c'è niente da segnalare. Si rispetta,
    # invece di ripiegare sulle frasi fisse per riempire lo spazio.
    return cleaned[:MAX_AI_INSIGHTS], "ai"
