"""Configurazione condivisa dei template Jinja2 + filtri di formattazione."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def static_version() -> str:
    """Impronta degli asset statici, per invalidare la cache dei browser.

    Senza questo, dopo un deploy i browser continuano a servire il CSS e il JS
    vecchi finché non si fa un hard refresh.

    Va ricalcolata a ogni render, non una volta all'import: in sviluppo
    `uvicorn --reload` riavvia solo quando cambia un file Python, quindi una
    modifica al solo CSS o JS lascerebbe la versione ferma e il browser
    continuerebbe a servire dalla cache. Costa una manciata di `stat()`.
    """
    try:
        newest = max(f.stat().st_mtime for f in STATIC_DIR.glob("*") if f.is_file())
        return str(int(newest))
    except (OSError, ValueError):
        return "0"


templates.env.globals["static_v"] = static_version


def _num(value) -> float | None:
    """Coercizione sicura a numero: gestisce None, Jinja Undefined e stringhe."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fmt_duration(seconds) -> str:
    n = _num(seconds)
    if not n:
        return "—"
    total = int(n)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {s:02d}s"


def fmt_km(meters) -> str:
    n = _num(meters)
    return f"{n / 1000:.2f} km" if n else "—"


def fmt_kg(grams) -> str:
    n = _num(grams)
    return f"{n / 1000:.1f} kg" if n else "—"


def fmt_num(value, decimals: int = 0) -> str:
    n = _num(value)
    if n is None:
        return "—"
    return f"{n:.{decimals}f}"


def fmt_pace(avg_speed) -> str:
    """Da m/s a min/km."""
    n = _num(avg_speed)
    if not n:
        return "—"
    sec_per_km = 1000 / n
    m, s = divmod(int(sec_per_km), 60)
    return f"{m}:{s:02d}/km"


templates.env.filters["duration"] = fmt_duration
templates.env.filters["km"] = fmt_km
templates.env.filters["kg"] = fmt_kg
templates.env.filters["num"] = fmt_num
templates.env.filters["pace"] = fmt_pace
