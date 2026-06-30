"""Readiness Score engine — Python puro, deterministico, zero AI."""
from __future__ import annotations

from dataclasses import dataclass

WEIGHTS = {"sleep": 0.30, "hrv": 0.25, "battery": 0.20, "load": 0.15, "rhr": 0.10}


@dataclass
class ReadinessFactor:
    name: str
    value: int
    color: str


@dataclass
class ReadinessResult:
    score: int | None
    label: str
    emoji: str
    recommendation: str
    breakdown: list[ReadinessFactor]


def _color(v: int) -> str:
    if v >= 70:
        return "green"
    if v >= 45:
        return "amber"
    return "red"


def _hrv_factor(status: str | None) -> int:
    if not status:
        return 65
    s = status.lower()
    if "balanc" in s or "good" in s:
        return 100
    if "low" in s or "unbalanc" in s or "poor" in s:
        return 40
    return 65


def _load_factor(ratio: float | None) -> int:
    if ratio is None:
        return 65
    if ratio > 1.5:
        return 30
    if ratio > 1.2:
        return 55
    if ratio < 0.8:
        return 70
    return 80


def _rhr_factor(rhr_7d: float | None, rhr_30d: float | None) -> int:
    if rhr_7d is None or rhr_30d is None:
        return 65
    if rhr_7d <= rhr_30d - 2:
        return 80
    if rhr_7d >= rhr_30d + 2:
        return 40
    return 65


def _clamp(v: float) -> int:
    return max(0, min(100, round(v)))


def _band(score: int) -> tuple[str, str, str]:
    if score >= 80:
        return "🔥", "Pronto", "Allenamento intenso / sessione chiave"
    if score >= 65:
        return "✅", "Buono", "Allenamento moderato"
    if score >= 50:
        return "🟡", "Discreto", "Corsa easy / attività leggera"
    if score >= 35:
        return "🔵", "Stanco", "Recovery run o riposo attivo"
    return "❌", "Riposo", "Riposo completo"


def compute_readiness(snap: dict) -> ReadinessResult:
    sleep = snap.get("sleep_score")
    battery = snap.get("body_battery_high")
    primary_present = any(v is not None for v in (
        sleep, snap.get("hrv_status"), battery,
        snap.get("load_ratio"), snap.get("resting_hr_7d_avg"),
    ))

    f_sleep = _clamp(sleep) if sleep is not None else 65
    f_hrv = _hrv_factor(snap.get("hrv_status"))
    f_batt = _clamp(battery) if battery is not None else 65
    f_load = _load_factor(snap.get("load_ratio"))
    f_rhr = _rhr_factor(snap.get("resting_hr_7d_avg"), snap.get("resting_hr_30d_avg"))

    breakdown = [
        ReadinessFactor("Sonno", f_sleep, _color(f_sleep)),
        ReadinessFactor("HRV", f_hrv, _color(f_hrv)),
        ReadinessFactor("Body Battery", f_batt, _color(f_batt)),
        ReadinessFactor("Carico", f_load, _color(f_load)),
        ReadinessFactor("FC riposo", f_rhr, _color(f_rhr)),
    ]

    if not primary_present:
        return ReadinessResult(None, "Dati assenti", "—",
                               "Premi Sincronizza per scaricare i tuoi dati Garmin.",
                               breakdown)

    raw = (f_sleep * WEIGHTS["sleep"] + f_hrv * WEIGHTS["hrv"]
           + f_batt * WEIGHTS["battery"] + f_load * WEIGHTS["load"]
           + f_rhr * WEIGHTS["rhr"])
    score = _clamp(raw)
    emoji, label, rec = _band(score)
    return ReadinessResult(score, label, emoji, rec, breakdown)
