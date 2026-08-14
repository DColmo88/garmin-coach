"""I prompt inviati al modello.

Qui si decide **quanti dati** arrivano all'AI, ed è il punto in cui si vince o
si perde la battaglia sui costi. La regola: il modello riceve una sintesi
compatta già calcolata in Python, mai serie di dati grezze. Se gli serve di
più, nella chat lo chiede con un tool (vedi `app/ai/tools.py`).
"""
from __future__ import annotations

from typing import Any

from app.ai.readiness import ReadinessResult
from app.db.models import UserGoal
from app.goals import describe_goal

TONE = (
    "Scrivi in italiano, dando del tu, con il tono di un allenatore competente "
    "che conosce l'atleta: diretto, concreto, mai motivazionale a vuoto. "
    "Niente emoji, niente elenchi puntati, niente frasi fatte come «ricorda che "
    "ogni corpo è diverso» o «ascolta il tuo corpo»."
)


def _fmt(value: Any, digits: int = 0, suffix: str = "") -> str:
    if value is None:
        return "n/d"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def snapshot_lines(snap: dict) -> str:
    """Lo stato dell'atleta in poche righe. Solo valori presenti."""
    rows: list[tuple[str, str]] = [
        ("Sonno (score ieri)", _fmt(snap.get("sleep_score"))),
        ("Sonno (media 7gg)", _fmt(snap.get("sleep_score_7d_avg"))),
        ("HRV", snap.get("hrv_status") or "n/d"),
        ("Body Battery max", _fmt(snap.get("body_battery_high"))),
        ("FC riposo", _fmt(snap.get("resting_hr_latest"), suffix=" bpm")),
        ("FC riposo media 7gg", _fmt(snap.get("resting_hr_7d_avg"), 1, " bpm")),
        ("FC riposo media 30gg", _fmt(snap.get("resting_hr_30d_avg"), 1, " bpm")),
        ("VO2max", _fmt(snap.get("vo2max_latest"), 1)),
        ("VO2max 4 settimane fa", _fmt(snap.get("vo2max_4w_ago"), 1)),
        ("Rapporto carico acuto/cronico", _fmt(snap.get("load_ratio"), 2)),
        ("Giorni dall'ultima corsa", _fmt(snap.get("days_since_last_run"))),
        ("Giorni attivi consecutivi", _fmt(snap.get("consecutive_active_days"))),
        ("Stress medio", _fmt(snap.get("avg_stress_latest"))),
    ]
    return "\n".join(f"- {label}: {value}" for label, value in rows if value != "n/d")


# --------------------------- coaching giornaliero ---------------------------


def coach_system_prompt() -> str:
    return (
        "Sei l'allenatore personale di un atleta amatoriale. Ogni mattina scrivi "
        "il messaggio che legge appena apre l'app.\n\n"
        f"{TONE}\n\n"
        "Vincoli:\n"
        "- Massimo 3 frasi.\n"
        "- Cita almeno un numero preciso dei suoi dati, per far capire che li hai letti.\n"
        "- Chiudi con l'indicazione pratica per oggi.\n"
        "- Il punteggio di prontezza e l'allenamento suggerito sono già stati "
        "calcolati: non contraddirli, spiegali.\n"
        "- Se c'è un obiettivo attivo, lega il consiglio di oggi a quello."
    )


def coach_user_prompt(
    snap: dict, readiness: ReadinessResult, goal: UserGoal | None, base
) -> str:
    """Il contesto del giorno: sintesi + verdetto deterministico + obiettivo."""
    factors = ", ".join(f"{f.name} {f.value}" for f in readiness.breakdown)
    return (
        f"Prontezza di oggi: {readiness.score}/100 ({readiness.label}).\n"
        f"Fattori: {factors}.\n\n"
        f"Dati recenti:\n{snapshot_lines(snap)}\n\n"
        f"Obiettivo: {describe_goal(goal)}\n\n"
        f"Allenamento già deciso: {base.workout.type}, {base.workout.duration}, "
        f"{base.workout.hr_zone}.\n\n"
        "Scrivi il messaggio del giorno."
    )


# --------------------------- chat ---------------------------


def chat_system_prompt(
    user_name: str, snap: dict, readiness: ReadinessResult, goal: UserGoal | None,
    today: str,
) -> str:
    """System prompt della chat: profilo + obiettivo + stato di oggi.

    Poche centinaia di token. Tutto il resto il modello se lo va a prendere
    con i tool, e solo se la conversazione lo richiede.
    """
    readiness_line = (
        f"{readiness.score}/100 ({readiness.label})"
        if readiness.score is not None
        else "non calcolabile (dati insufficienti)"
    )
    return (
        f"Sei l'allenatore personale di {user_name}. Oggi è {today}.\n\n"
        f"{TONE}\n\n"
        "COME LAVORI\n"
        "Hai già sotto mano lo stato di oggi (sotto). Per tutto il resto — periodi "
        "passati, singoli allenamenti, andamenti di lungo periodo — usa i tuoi "
        "strumenti invece di tirare a indovinare o di dire che non hai i dati. "
        "Chiama più strumenti insieme quando servono più informazioni.\n"
        "Se un dato non c'è nemmeno dopo aver cercato, dillo chiaramente: meglio "
        "«non hai misurazioni di peso» che un numero inventato.\n"
        "Rispondi in modo conciso. Le domande semplici meritano risposte brevi, "
        "non un tema.\n\n"
        "COSA NON FAI\n"
        "Non sei un medico. Se emergono sintomi che possono essere clinici "
        "(dolore persistente, aritmie, svenimenti) dillo in una riga e suggerisci "
        "di sentire un medico, senza allarmismi e senza diagnosi.\n\n"
        f"OBIETTIVO ATTIVO\n{describe_goal(goal)}\n\n"
        f"STATO DI OGGI\nProntezza: {readiness_line}\n{snapshot_lines(snap)}"
    )


# --------------------------- piani di allenamento ---------------------------


def plan_system_prompt(structured: bool = False) -> str:
    formato = (
        "Rispondi solo con il JSON richiesto, senza testo attorno. Una voce per "
        "ogni giorno della settimana, riposo compreso: il calendario deve essere "
        "completo."
        if structured else
        "Formato: markdown. Una tabella per settimana (giorno, tipo di sessione, "
        "durata o distanza, zona FC, nota). Prima delle tabelle, tre righe che "
        "spiegano la logica del piano."
    )
    return (
        "Sei un allenatore di corsa certificato. Costruisci piani di allenamento "
        "realistici a partire dai dati reali dell'atleta.\n\n"
        f"{TONE}\n\n"
        "Regole non negoziabili:\n"
        "- Non aumentare il volume settimanale di oltre il 10% rispetto alla settimana prima.\n"
        "- Almeno un giorno di riposo completo a settimana.\n"
        "- Le ultime due settimane prima di una gara sono di scarico.\n"
        "- Parti dal volume che l'atleta regge davvero adesso, non da quello che vorrebbe.\n"
        "- Se i dati dicono che l'obiettivo non è raggiungibile nei tempi, dillo "
        "in apertura e proponi un traguardo intermedio.\n\n"
        + formato
    )


def plan_user_prompt(context: dict[str, Any], goal: str) -> str:
    """Per il piano servono i numeri di allenamento, non tutta la serie storica."""
    activities = context.get("activities", [])[:20]
    training = context.get("training", [])

    total_km = sum((a.get("distance_m") or 0) for a in activities) / 1000
    runs = [a for a in activities if "run" in (a.get("type") or "").lower()]
    longest_km = max((a.get("distance_m") or 0) for a in activities) / 1000 if activities else 0

    vo2 = next((t.get("vo2max") for t in reversed(training) if t.get("vo2max")), None)
    status = next(
        (t.get("training_status") for t in reversed(training) if t.get("training_status")), None
    )

    recent = "\n".join(
        f"- {a.get('start_time', '')[:10]} · {a.get('type')} · "
        f"{(a.get('distance_m') or 0) / 1000:.1f} km · "
        f"{(a.get('duration_sec') or 0) / 60:.0f} min · FC media {_fmt(a.get('avg_hr'))}"
        for a in activities[:10]
    )

    return (
        f"Obiettivo: {goal}\n\n"
        "Stato dell'atleta:\n"
        f"- VO2max: {_fmt(vo2, 1)}\n"
        f"- Training status Garmin: {status or 'n/d'}\n"
        f"- Ultime {len(activities)} attività: {total_km:.0f} km totali, "
        f"di cui {len(runs)} corse\n"
        f"- Uscita più lunga: {longest_km:.1f} km\n\n"
        f"Allenamenti recenti:\n{recent or 'nessuno registrato'}\n\n"
        "Costruisci il piano."
    )
