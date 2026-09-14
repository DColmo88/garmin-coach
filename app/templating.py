"""Configurazione condivisa dei template Jinja2 + filtri di formattazione."""
from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.requests import Request

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"


def csp_context(request: Request) -> dict:
    """Il nonce della richiesta, per gli `<script>` inline.

    Lo mette `SecurityHeadersMiddleware` su `request.state`. Il default vuoto
    serve ai test che rendono un template senza passare dal middleware: senza,
    Jinja scriverebbe `nonce="Undefined"` e lo script verrebbe bloccato.
    """
    return {"csp_nonce": getattr(request.state, "csp_nonce", "")}


def navigation_context(request: Request) -> dict:
    """Menu e sezioni visibili, calcolati una volta per richiesta.

    Sta in un *context processor* invece che nelle singole rotte perché deve
    valere per tutte: basterebbe un template reso da una rotta distratta per
    ritrovarsi «Sonno» nel menu di chi usa Strava, e quella pagina sarebbe
    vuota. Meglio un punto solo che quindici da ricordare.
    """
    from app import navigation, providers
    from app.auth.session import SESSION_COOKIE, read_session_token
    from app.db.database import SessionLocal
    from app.db.models import User

    empty = {"sections": frozenset(), "nav_groups": navigation.groups_for(()),
             "account_items": navigation.ACCOUNT_ITEMS}

    token = request.cookies.get(SESSION_COOKIE)
    parsed = read_session_token(token) if token else None
    if parsed is None:
        return empty
    user_id, epoch = parsed

    db = SessionLocal()
    try:
        user = db.get(User, user_id)
        # Le stesse tre condizioni di `optional_user`: il menu non deve
        # comparire per un account disattivato o per un cookie revocato.
        if user is None or not user.is_active or (user.session_epoch or 0) != epoch:
            return empty
        sections = providers.visible_sections(db, user)
        return {
            "sections": sections,
            "nav_groups": navigation.groups_for(sections),
            "account_items": navigation.ACCOUNT_ITEMS,
        }
    finally:
        db.close()


templates = Jinja2Templates(
    directory=BASE_DIR / "templates",
    context_processors=[navigation_context, csp_context],
)


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


def fmt_training_status(raw) -> str:
    """«UNPRODUCTIVE_1» → «allenamento improduttivo»."""
    from app.insights import training_status_label

    return training_status_label(raw) or "—"


def chat_segments(text) -> list:
    """Divide un messaggio del coach in prosa e schede di allenamento."""
    from app.ai.workout_card import split

    return split(text or "")


def sport_icon(activity) -> str:
    """Il nome dell'icona per il tipo di attività."""
    from app.sports import icon_of

    return icon_of(activity)


def sport_label(activity) -> str:
    """«indoor_cycling» → «Bici indoor»."""
    from app.sports import label_of

    return label_of(activity)


def sport_speed(activity) -> str:
    """Ritmo per la corsa, km/h per la bici. Vuoto se non calcolabile."""
    from app.sports import speed_label

    return speed_label(activity) or "—"


templates.env.filters["duration"] = fmt_duration
templates.env.filters["training_status"] = fmt_training_status
templates.env.filters["chat_segments"] = chat_segments
templates.env.filters["sport_icon"] = sport_icon
templates.env.filters["sport_label"] = sport_label
templates.env.filters["sport_speed"] = sport_speed
templates.env.filters["km"] = fmt_km
templates.env.filters["kg"] = fmt_kg
templates.env.filters["num"] = fmt_num
templates.env.filters["pace"] = fmt_pace
