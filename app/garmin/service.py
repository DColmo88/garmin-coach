"""Letture 'live' da Garmin per i dati snapshot (non storicizzati nel DB).

Esempi: dispositivi, gear, record personali, profilo, dettaglio di una
singola attività. Una cache in memoria con TTL evita di martellare l'API
a ogni refresh di pagina. Le chiavi di cache includono l'id utente, così i
dati di un utente non possono mai finire nella pagina di un altro.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any, Callable

from app.db.models import User
from app.clock import today_for
from app.garmin.client import get_client

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, Any]] = {}
_TTL_SECONDS = 300  # 5 minuti


def _cached(user_id: int, key: str, producer: Callable[[], Any], ttl: int = _TTL_SECONDS) -> Any:
    full_key = f"{user_id}:{key}"
    now = time.time()
    hit = _CACHE.get(full_key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    value = producer()
    _CACHE[full_key] = (now, value)
    return value


def clear_cache(user_id: int | None = None) -> None:
    """Svuota la cache di un utente, o di tutti se `user_id` è None."""
    if user_id is None:
        _CACHE.clear()
        return
    prefix = f"{user_id}:"
    for key in [k for k in _CACHE if k.startswith(prefix)]:
        del _CACHE[key]


def _safe(producer: Callable[[], Any], default: Any) -> Any:
    """Esegue una chiamata API tollerando errori (ritorna default)."""
    try:
        return producer()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Chiamata Garmin fallita: %s", exc)
        return default


# --------------------------- profilo / dispositivi ---------------------------

def get_overview_snapshot(user: User) -> dict[str, Any]:
    """Nome utente + dispositivo usato più di recente (per l'header)."""
    def producer():
        c = get_client(user)
        return {
            "full_name": _safe(c.get_full_name, None),
            "last_device": _safe(c.get_device_last_used, {}),
        }

    return _cached(user.id, "overview_snapshot", producer)


def get_devices(user: User) -> list[dict]:
    return _cached(user.id, "devices", lambda: _safe(get_client(user).get_devices, []))


def get_profile(user: User) -> dict:
    return _cached(user.id, "profile", lambda: _safe(get_client(user).get_user_profile, {}))


# --------------------------- gear ---------------------------

def get_gear_overview(user: User) -> list[dict]:
    """Lista gear con statistiche aggregate."""
    def producer():
        c = get_client(user)
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

    return _cached(user.id, "gear_overview", producer)


# --------------------------- performance snapshot ---------------------------

def get_performance_snapshot(user: User) -> dict[str, Any]:
    """Record personali + previsioni di gara + endurance/hill score."""
    def producer():
        c = get_client(user)
        today = today_for(user).isoformat()
        return {
            "personal_records": _safe(c.get_personal_record, []),
            "race_predictions": _safe(c.get_race_predictions, {}),
            "endurance_score": _safe(lambda: c.get_endurance_score(today), {}),
            "hill_score": _safe(lambda: c.get_hill_score(today), {}),
            "primary_device": _safe(c.get_primary_training_device, {}),
        }

    return _cached(user.id, "performance_snapshot", producer)


# --------------------------- badge / sfide ---------------------------

def get_badges(user: User) -> dict[str, Any]:
    def producer():
        c = get_client(user)
        return {
            "earned": _safe(c.get_earned_badges, []),
            "adhoc": _safe(lambda: c.get_adhoc_challenges(0, 10), []),
        }

    return _cached(user.id, "badges", producer)


# --------------------------- dettaglio attività ---------------------------

def get_activity_full(user: User, activity_id: int) -> dict[str, Any]:
    """Tutti i dettagli di una singola attività (no cache: on-demand)."""
    c = get_client(user)
    return {
        "summary": _safe(lambda: c.get_activity(activity_id), {}),
        "splits": _safe(lambda: c.get_activity_splits(activity_id), {}),
        "hr_zones": _safe(lambda: c.get_activity_hr_in_timezones(activity_id), []),
        "weather": _safe(lambda: c.get_activity_weather(activity_id), {}),
        "gear": _safe(lambda: c.get_activity_gear(activity_id), []),
        "exercise_sets": _safe(lambda: c.get_activity_exercise_sets(activity_id), {}),
    }
