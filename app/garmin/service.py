"""Letture 'live' da Garmin per i dati snapshot (non storicizzati nel DB).

Esempi: dispositivi, gear, record personali, profilo, dettaglio di una
singola attività. Una cache in memoria con TTL evita di martellare l'API
a ogni refresh di pagina.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Callable

from app.garmin.client import get_client

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL_SECONDS = 300  # 5 minuti


def _cached(key: str, producer: Callable[[], Any], ttl: int = _TTL_SECONDS) -> Any:
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    _CACHE[key] = (now, value)
    return value


def clear_cache() -> None:
    _CACHE.clear()


def _safe(producer: Callable[[], Any], default: Any) -> Any:
    """Esegue una chiamata API tollerando errori (ritorna default)."""
    try:
        return producer()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Chiamata Garmin fallita: %s", exc)
        return default


# --------------------------- profilo / dispositivi ---------------------------

def get_overview_snapshot() -> dict[str, Any]:
    """Nome utente + dispositivo usato più di recente (per l'header)."""
    def producer():
        c = get_client()
        return {
            "full_name": _safe(c.get_full_name, None),
            "last_device": _safe(c.get_device_last_used, {}),
        }

    return _cached("overview_snapshot", producer)


def get_devices() -> list[dict]:
    return _cached("devices", lambda: _safe(get_client().get_devices, []))


def get_profile() -> dict:
    return _cached("profile", lambda: _safe(get_client().get_user_profile, {}))


# --------------------------- gear ---------------------------

def get_gear_overview() -> list[dict]:
    """Lista gear con statistiche aggregate."""
    def producer():
        c = get_client()
        profile = _safe(c.get_user_profile, {}) or {}
        user_number = profile.get("userProfileNumber") or profile.get("id")
        if not user_number:
            return []
        gears = _safe(lambda: c.get_gear(user_number), []) or []
        out = []
        for g in gears:
            uuid = g.get("uuid") or g.get("gearPk")
            stats = _safe(lambda u=uuid: c.get_gear_stats(u), {}) if uuid else {}
            out.append({"gear": g, "stats": stats})
        return out

    return _cached("gear_overview", producer)


# --------------------------- performance snapshot ---------------------------

def get_performance_snapshot() -> dict[str, Any]:
    """Record personali + previsioni di gara + endurance/hill score."""
    def producer():
        c = get_client()
        today = date.today().isoformat()
        return {
            "personal_records": _safe(c.get_personal_record, []),
            "race_predictions": _safe(c.get_race_predictions, {}),
            "endurance_score": _safe(lambda: c.get_endurance_score(today), {}),
            "hill_score": _safe(lambda: c.get_hill_score(today), {}),
            "primary_device": _safe(c.get_primary_training_device, {}),
        }

    return _cached("performance_snapshot", producer)


# --------------------------- badge / sfide ---------------------------

def get_badges() -> dict[str, Any]:
    def producer():
        c = get_client()
        return {
            "earned": _safe(c.get_earned_badges, []),
            "adhoc": _safe(lambda: c.get_adhoc_challenges(0, 10), []),
        }

    return _cached("badges", producer)


# --------------------------- dettaglio attività ---------------------------

def get_activity_full(activity_id: int) -> dict[str, Any]:
    """Tutti i dettagli di una singola attività (no cache: on-demand)."""
    c = get_client()
    return {
        "summary": _safe(lambda: c.get_activity(activity_id), {}),
        "splits": _safe(lambda: c.get_activity_splits(activity_id), {}),
        "hr_zones": _safe(lambda: c.get_activity_hr_in_timezones(activity_id), []),
        "weather": _safe(lambda: c.get_activity_weather(activity_id), {}),
        "gear": _safe(lambda: c.get_activity_gear(activity_id), []),
        "exercise_sets": _safe(lambda: c.get_activity_exercise_sets(activity_id), {}),
    }
