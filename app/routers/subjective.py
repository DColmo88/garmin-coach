"""Quello che l'atleta dice di sé: check-in del mattino e sforzo percepito.

Due moduli minuscoli con una responsabilità grossa: sono l'unica cosa in tutta
l'app che non arriva da un apparecchio, e in letteratura battono HRV e
frequenza a riposo nel predire l'affaticamento.

Le regole di interfaccia che ne derivano, entrambe scelte perché sia una cosa
che si fa e non una cosa che si abbandona dopo tre giorni:

- **niente campi obbligatori.** Chi risponde a una domanda su quattro dà
  comunque un'informazione; pretendere le altre tre significa non riceverne
  nessuna;
- **il check-in si sovrascrive.** Se lo apri due volte nello stesso giorno,
  l'ultima risposta vince invece di dare un errore di duplicato: alle sette di
  mattina uno può sbagliare uno slider.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.session import require_user
from app.clock import naive_utc, today_for
from app.db.database import get_session
from app.db.models import Activity, DailyCheckin, User

router = APIRouter()

# Le risposte vanno da 1 a 5, lo sforzo da 1 a 10 (Borg CR10).
SCALE_MIN, SCALE_MAX = 1, 5
RPE_MIN, RPE_MAX = 1, 10
NOTE_MAX = 500


def _slider(raw: str, low: int = SCALE_MIN, high: int = SCALE_MAX) -> int | None:
    """Un intero dentro la scala, oppure `None` se il campo è vuoto o assurdo.

    Un valore fuori scala diventa `None` e non viene salvato: è meglio non
    avere la risposta che averne una che falsa la media di tutte le altre.
    """
    value = (raw or "").strip()
    if not value:
        return None
    try:
        number = int(value)
    except ValueError:
        return None
    return number if low <= number <= high else None


def _note(raw: str) -> str | None:
    text = (raw or "").strip()
    return text[:NOTE_MAX] or None


@router.post("/checkin")
def save_checkin(
    energy: str = Form(""),
    legs: str = Form(""),
    mood: str = Form(""),
    sleep_quality: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Salva il check-in di oggi. Ripeterlo sovrascrive, non duplica."""
    today = today_for(user)
    row = db.scalar(
        select(DailyCheckin).where(
            DailyCheckin.user_id == user.id, DailyCheckin.day == today
        )
    )
    if row is None:
        row = DailyCheckin(user_id=user.id, day=today)
        db.add(row)

    row.energy = _slider(energy)
    row.legs = _slider(legs)
    row.mood = _slider(mood)
    row.sleep_quality = _slider(sleep_quality)
    row.note = _note(note)
    row.created_at = naive_utc()
    db.commit()

    # La prontezza di oggi cambia appena il check-in arriva: ricalcolarla qui
    # evita che l'atleta veda ancora il punteggio di prima di aver risposto.
    # È deterministica e gratis; l'AI non viene toccata.
    _refresh_readiness(db, user, today)

    return RedirectResponse("/coach", status_code=303)


def _refresh_readiness(db: Session, user: User, today) -> None:
    from app import queries as q
    from app.ai.readiness import compute_readiness
    from app.db.models import DailyCoachCache

    row = db.scalar(
        select(DailyCoachCache).where(
            DailyCoachCache.user_id == user.id, DailyCoachCache.day == today
        )
    )
    if row is None:
        return
    readiness = compute_readiness(q.coach_snapshot(db, user.id, today))
    row.readiness_score = readiness.score
    row.readiness_label = readiness.label
    db.commit()


@router.post("/activities/{activity_id}/effort")
def save_effort(
    activity_id: int,
    rpe: str = Form(""),
    feel_note: str = Form(""),
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Lo sforzo percepito di una singola seduta.

    Cambia il carico di quell'attività — e quindi forma, rapporto
    acuto/cronico e XP — perché `activity_load` lo usa quando potenza e
    frequenza cardiaca non ci sono.
    """
    activity = db.scalar(
        select(Activity).where(
            Activity.user_id == user.id, Activity.external_id == activity_id
        )
    )
    if activity is None:
        return RedirectResponse("/activities", status_code=303)

    activity.rpe = _slider(rpe, RPE_MIN, RPE_MAX)
    activity.feel_note = _note(feel_note)
    db.commit()
    return RedirectResponse(f"/activities/{activity_id}", status_code=303)


# ============================================================================
# Letture, per le pagine
# ============================================================================


def todays_checkin(db: Session, user: User) -> DailyCheckin | None:
    """Il check-in di oggi, se è già stato compilato."""
    return db.scalar(
        select(DailyCheckin).where(
            DailyCheckin.user_id == user.id, DailyCheckin.day == today_for(user)
        )
    )


def activities_awaiting_effort(db: Session, user: User, days: int = 3) -> list[Activity]:
    """Le sedute recenti senza sforzo percepito.

    Serve a chiedere l'RPE dove la domanda ha senso — subito dopo — invece che
    con una notifica generica: a tre giorni di distanza ci si ricorda della
    distanza, non della fatica.
    """
    from app.clock import day_bounds

    since, _ = day_bounds(today_for(user) - timedelta(days=days))
    return list(db.scalars(
        select(Activity)
        .where(
            Activity.user_id == user.id,
            Activity.rpe.is_(None),
            Activity.start_time >= since,
        )
        .order_by(Activity.start_time.desc())
        .limit(5)
    ).all())
