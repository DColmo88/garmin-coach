"""Insight rules engine — Python puro, zero AI, zero latenza."""
from __future__ import annotations

from dataclasses import dataclass

_PRIORITY = {"red": 0, "amber": 1, "green": 2}


@dataclass
class Insight:
    icon: str
    title: str
    text: str
    color: str
    priority: int


def _mk(icon: str, title: str, text: str, color: str) -> Insight:
    return Insight(icon, title, text, color, _PRIORITY[color])


def detect_insights(snap: dict) -> list[Insight]:
    out: list[Insight] = []

    rhr7, rhr30 = snap.get("resting_hr_7d_avg"), snap.get("resting_hr_30d_avg")
    if rhr7 is not None and rhr30 is not None:
        if rhr7 <= rhr30 - 3:
            out.append(_mk("📈", "FC riposo in calo",
                           f"FC a riposo {round(rhr30 - rhr7)} bpm più bassa del mese scorso "
                           "— fitness in crescita.", "green"))
        elif rhr7 >= rhr30 + 4:
            out.append(_mk("⚠️", "FC riposo in salita",
                           "FC a riposo più alta del solito — possibile stanchezza o stress.",
                           "amber"))

    ratio = snap.get("load_ratio")
    if ratio is not None and ratio >= 1.4:
        out.append(_mk("⚠️", "Carico acuto alto",
                       "Carico acuto alto rispetto al cronico — rischio overtraining, "
                       "valuta un giorno easy o di riposo.", "red"))

    ss7 = snap.get("sleep_score_7d_avg")
    if ss7 is not None and ss7 < 65:
        out.append(_mk("🌙", "Sonno sotto la media",
                       "Qualità del sonno bassa questa settimana — priorità al recupero.",
                       "amber"))

    vo2_now, vo2_old = snap.get("vo2max_latest"), snap.get("vo2max_4w_ago")
    if vo2_now is not None and vo2_old is not None and vo2_now > vo2_old:
        out.append(_mk("📈", "VO₂max in crescita",
                       f"VO₂max +{round(vo2_now - vo2_old, 1)} rispetto a un mese fa "
                       "— sei più forte.", "green"))

    dslr = snap.get("days_since_last_run")
    if dslr is not None and dslr > 5:
        out.append(_mk("🟢", "Corpo riposato",
                       f"{dslr} giorni senza corsa — il corpo è riposato, momento perfetto "
                       "per ripartire.", "green"))

    streak = snap.get("consecutive_active_days") or 0
    if streak >= 7:
        out.append(_mk("🔥", "In serie",
                       f"{streak} giorni consecutivi attivi — continua così!", "green"))

    deep = snap.get("deep_pct")
    if deep is not None and deep < 0.15:
        out.append(_mk("🌙", "Sonno profondo basso",
                       "Sonno profondo sotto il 15% — prova un rituale serale più regolare.",
                       "amber"))

    bb_low = snap.get("body_battery_low_7d_avg")
    if bb_low is not None and bb_low < 20:
        out.append(_mk("🔋", "Energia a terra",
                       "Body Battery quasi a zero ogni sera — forse stai facendo troppo.",
                       "amber"))

    return out


def top_insights(snap: dict, n: int = 3) -> list[Insight]:
    ordered = sorted(detect_insights(snap), key=lambda i: i.priority)
    return ordered[:n]
