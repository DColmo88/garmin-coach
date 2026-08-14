"""Router delle pagine HTML della dashboard."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app import goals
from app import queries as q
from app.ai.insights import Insight
from app.insights import (
    read_activities,
    read_body,
    read_health,
    read_performance,
    read_sleep,
)
from app.ai.provider import get_provider
from app.ai.readiness import compute_readiness
from app.auth.session import require_user
from app.db.database import get_session
from app.db.models import User
from app.garmin import service
from app.garmin.client import GarminClientError
from app.pipeline import refresh_daily_cache
from app import training
from app.templating import templates

router = APIRouter()


def _ctx(request: Request, active: str, user: User, **extra) -> dict:
    """Contesto comune a tutti i template: richiesta, voce di menu attiva, utente."""
    base = {
        "request": request,
        "active": active,
        "user": user,
        # Dalla v2 le credenziali sono per-utente: chi è loggato è per definizione collegato.
        "garmin_configured": True,
    }
    base.update(extra)
    return base


# --------------------------- Coach ---------------------------

@router.get("/coach", response_class=HTMLResponse)
def coach(request: Request, db: Session = Depends(get_session),
          user: User = Depends(require_user)):
    snap = q.coach_snapshot(db, user.id)
    readiness = compute_readiness(snap)
    goal = goals.active_goal(db, user.id)

    # Il messaggio del coach arriva dalla cache del giorno: aprire la pagina
    # dieci volte non deve costare dieci chiamate all'AI.
    cached = refresh_daily_cache(db, user)
    insights = [
        Insight(i["icon"], i["title"], i["text"], i["color"], 0)
        for i in (cached.insights_json or [])
    ]
    workout = cached.workout_json or {}
    coaching = SimpleNamespace(
        message=cached.coach_message or readiness.recommendation,
        workout=SimpleNamespace(
            icon=workout.get("icon", "—"),
            type=workout.get("type", "—"),
            duration=workout.get("duration", "—"),
            hr_zone=workout.get("hr_zone", "—"),
            note=workout.get("note", ""),
        ),
    )

    def _r(v):
        return round(v) if v is not None else None

    mini = [
        {"value": _r(snap.get("sleep_score")), "label": "Sonno"},
        {"value": _r(snap.get("body_battery_high")), "label": "Battery"},
        {"value": _r(snap.get("vo2max_latest")), "label": "VO₂max", "vmax": 70},
    ]
    from app import gamification

    progress = gamification.progress_of(db, user)
    in_range = sum(1 for f in readiness.breakdown if f.color == "green")
    return templates.TemplateResponse(
        "coach.html",
        _ctx(request, "coach", user, snap=snap, readiness=readiness, insights=insights,
             coaching=coaching, mini=mini, in_range=in_range,
             goal=goal, days_left=goals.days_to_target(goal) if goal else None,
             progress=progress,
             total_metrics=len(readiness.breakdown)),
    )


# --------------------------- Panoramica ---------------------------

@router.get("/", response_class=HTMLResponse)
def overview(request: Request, db: Session = Depends(get_session),
             user: User = Depends(require_user)):
    wellness = q.wellness_series(db, user.id, 28)
    sleep = q.sleep_series(db, user.id, 28)
    training = q.training_series(db, user.id, 28)
    activities = q.recent_activities(db, user.id, 5)

    chart = {
        "labels": q.labels(wellness),
        "steps": q.values(wellness, "total_steps"),
        "resting_hr": q.values(wellness, "resting_hr"),
        "stress": q.values(wellness, "avg_stress"),
        "body_battery_high": q.values(wellness, "body_battery_high"),
        "sleep_labels": q.labels(sleep),
        "sleep_score": q.values(sleep, "sleep_score"),
        "vo2_labels": q.labels(training),
        "vo2max": q.values(training, "vo2max"),
    }
    kpis = {
        "steps": q.latest(wellness, "total_steps"),
        "resting_hr": q.latest(wellness, "resting_hr"),
        "body_battery": q.latest(wellness, "body_battery_high"),
        "sleep_score": q.latest(sleep, "sleep_score"),
        "vo2max": q.latest(training, "vo2max"),
        "readiness": q.latest(training, "readiness_score"),
        "training_status": q.latest(training, "training_status"),
        "stress": q.latest(wellness, "avg_stress"),
    }
    return templates.TemplateResponse(
        "overview.html",
        _ctx(request, "overview", user, chart=chart, kpis=kpis, activities=activities,
             has_data=bool(wellness or training)),
    )


# --------------------------- Attività ---------------------------

@router.get("/activities", response_class=HTMLResponse)
def activities(request: Request, db: Session = Depends(get_session),
               user: User = Depends(require_user)):
    rows = q.recent_activities(db, user.id, 50)
    by_type: dict[str, int] = {}
    for a in rows:
        by_type[a.activity_type or "altro"] = by_type.get(a.activity_type or "altro", 0) + 1
    chart = {"type_labels": list(by_type.keys()), "type_counts": list(by_type.values())}
    return templates.TemplateResponse(
        "activities.html",
        _ctx(request, "activities", user, activities=rows, chart=chart,
             reading=read_activities(rows)),
    )


@router.get("/activities/{activity_id}", response_class=HTMLResponse)
def activity_detail(activity_id: int, request: Request,
                    db: Session = Depends(get_session),
                    user: User = Depends(require_user)):
    activity = q.get_activity(db, user.id, activity_id)
    detail = None
    error = None
    try:
        detail = service.get_activity_full(user, activity_id)
    except GarminClientError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        error = f"Impossibile caricare i dettagli live: {exc}"
    return templates.TemplateResponse(
        "activity_detail.html",
        _ctx(request, "activities", user, activity=activity, detail=detail, error=error,
             activity_id=activity_id),
    )


# --------------------------- Sonno ---------------------------

@router.get("/sleep", response_class=HTMLResponse)
def sleep(request: Request, db: Session = Depends(get_session),
          user: User = Depends(require_user)):
    rows = q.sleep_series(db, user.id, 28)
    chart = {
        "labels": q.labels(rows),
        "deep": [(v or 0) / 3600 for v in q.values(rows, "deep_sleep_sec")],
        "light": [(v or 0) / 3600 for v in q.values(rows, "light_sleep_sec")],
        "rem": [(v or 0) / 3600 for v in q.values(rows, "rem_sleep_sec")],
        "awake": [(v or 0) / 3600 for v in q.values(rows, "awake_sec")],
        "score": q.values(rows, "sleep_score"),
    }
    return templates.TemplateResponse(
        "sleep.html",
        _ctx(request, "sleep", user, rows=list(reversed(rows)), chart=chart,
             reading=read_sleep(rows)),
    )


# --------------------------- Salute ---------------------------

@router.get("/health", response_class=HTMLResponse)
def health(request: Request, db: Session = Depends(get_session),
           user: User = Depends(require_user)):
    rows = q.wellness_series(db, user.id, 28)
    chart = {
        "labels": q.labels(rows),
        "resting_hr": q.values(rows, "resting_hr"),
        "min_hr": q.values(rows, "min_hr"),
        "max_hr": q.values(rows, "max_hr"),
        "stress": q.values(rows, "avg_stress"),
        "body_battery_high": q.values(rows, "body_battery_high"),
        "body_battery_low": q.values(rows, "body_battery_low"),
        "spo2": q.values(rows, "avg_spo2"),
        "respiration": q.values(rows, "avg_respiration"),
        "steps": q.values(rows, "total_steps"),
        "floors": q.values(rows, "floors_up"),
        "moderate": q.values(rows, "moderate_intensity_min"),
        "vigorous": q.values(rows, "vigorous_intensity_min"),
    }
    return templates.TemplateResponse(
        "health.html",
        _ctx(request, "health", user, rows=list(reversed(rows)), chart=chart,
             reading=read_health(rows)),
    )


# --------------------------- Corpo ---------------------------

@router.get("/body", response_class=HTMLResponse)
def body(request: Request, db: Session = Depends(get_session),
         user: User = Depends(require_user)):
    rows = q.body_series(db, user.id, 90)
    chart = {
        "labels": q.labels(rows),
        "weight": [(v or 0) / 1000 if v else None for v in q.values(rows, "weight_g")],
        "body_fat": q.values(rows, "body_fat_pct"),
        "muscle": [(v or 0) / 1000 if v else None for v in q.values(rows, "muscle_mass_g")],
        "bmi": q.values(rows, "bmi"),
    }
    return templates.TemplateResponse(
        "body.html",
        _ctx(request, "body", user, rows=list(reversed(rows)), chart=chart,
             reading=read_body(rows)),
    )


# --------------------------- Performance ---------------------------

@router.get("/performance", response_class=HTMLResponse)
def performance(request: Request, db: Session = Depends(get_session),
                user: User = Depends(require_user)):
    rows = q.training_series(db, user.id, 28)
    chart = {
        "labels": q.labels(rows),
        "vo2max": q.values(rows, "vo2max"),
        "vo2max_cycling": q.values(rows, "vo2max_cycling"),
        "training_load": q.values(rows, "training_load"),
        "hrv": q.values(rows, "hrv_weekly_avg"),
        "readiness": q.values(rows, "readiness_score"),
    }
    snapshot = None
    error = None
    try:
        snapshot = service.get_performance_snapshot(user)
    except GarminClientError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        error = f"Dati live non disponibili: {exc}"
    return templates.TemplateResponse(
        "performance.html",
        _ctx(request, "performance", user, rows=list(reversed(rows)), chart=chart,
             snapshot=snapshot, error=error, reading=read_performance(rows)),
    )


# --------------------------- Dispositivi & Gear ---------------------------

@router.get("/devices", response_class=HTMLResponse)
def devices(request: Request, user: User = Depends(require_user)):
    data = {"devices": [], "gear": [], "badges": {}}
    error = None
    try:
        data["devices"] = service.get_devices(user)
        data["gear"] = service.get_gear_overview(user)
        data["badges"] = service.get_badges(user)
    except GarminClientError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        error = f"Dati live non disponibili: {exc}"
    return templates.TemplateResponse(
        "devices.html",
        _ctx(request, "devices", user, data=data, error=error),
    )


# --------------------------- Obiettivi ---------------------------

@router.get("/goals", response_class=HTMLResponse)
def goals_page(request: Request, db: Session = Depends(get_session),
               user: User = Depends(require_user), saved: str = ""):
    current = goals.active_goal(db, user.id)
    return templates.TemplateResponse(
        "goals.html",
        _ctx(request, "goals", user,
             goal_types=goals.GOAL_TYPES,
             current=current,
             current_type=goals.get_goal_type(current.goal_type) if current else None,
             days_left=goals.days_to_target(current) if current else None,
             past=goals.past_goals(db, user.id),
             saved=bool(saved)),
    )


@router.post("/goals")
async def goals_save(request: Request, db: Session = Depends(get_session),
                     user: User = Depends(require_user)):
    """Salva l'obiettivo attivo. I parametri variano per tipo, quindi si legge
    il form grezzo invece di dichiarare un campo per ognuno."""
    form = await request.form()
    goal_type_key = str(form.get("goal_type") or "")

    raw_date = str(form.get("target_date") or "").strip()
    target_date = None
    if raw_date:
        try:
            target_date = date.fromisoformat(raw_date)
        except ValueError:
            target_date = None

    # I campi dei parametri sono prefissati con "p_" per non confondersi
    # con title/target_date/priority_notes.
    params = {k[2:]: v for k, v in form.items() if k.startswith("p_")}

    try:
        goals.set_active_goal(
            db, user, goal_type_key,
            title=str(form.get("title") or ""),
            target_date=target_date,
            params=params,
            priority_notes=str(form.get("priority_notes") or ""),
        )
    except ValueError:
        return RedirectResponse("/goals", status_code=303)
    return RedirectResponse("/goals?saved=1", status_code=303)


@router.post("/goals/close")
async def goals_close(request: Request, db: Session = Depends(get_session),
                      user: User = Depends(require_user)):
    form = await request.form()
    goals.close_goal(db, user.id, outcome=str(form.get("outcome") or ""))
    return RedirectResponse("/goals", status_code=303)


# --------------------------- Piano di allenamento ---------------------------

@router.get("/plan", response_class=HTMLResponse)
def plan_page(request: Request, db: Session = Depends(get_session),
              user: User = Depends(require_user), error: str = ""):
    plan = training.get_active_plan(db, user.id)
    goal = goals.active_goal(db, user.id)

    week = current = session = None
    if plan is not None:
        current = training.current_week_number(plan)
        week = training.week_data(plan, current)
        cached = refresh_daily_cache(db, user)
        score = int(cached.readiness_score) if cached.readiness_score is not None else None
        session = training.today_session(plan, score)

    from app.ai import usage

    return templates.TemplateResponse(
        "plan.html",
        _ctx(request, "plan", user,
             plan=plan, goal=goal, week=week, current_week=current,
             today_session=session,
             weekdays=training.WEEKDAYS,
             today_name=training.WEEKDAYS[date.today().weekday()],
             progress=training.progress_pct(plan) if plan else 0,
             plans_left=usage.remaining(db, user, "plan"),
             error=error),
    )


@router.post("/plan/generate")
def plan_generate(db: Session = Depends(get_session),
                  user: User = Depends(require_user)):
    """Genera il piano. È l'unica azione che consuma la quota mensile."""
    from app.ai import usage

    try:
        training.generate_plan(db, user)
    except usage.QuotaExceeded as exc:
        return RedirectResponse(
            f"/plan?error=Hai esaurito i piani di questo mese ({exc.used}/{exc.limit}).",
            status_code=303,
        )
    except training.PlanError as exc:
        return RedirectResponse(f"/plan?error={exc}", status_code=303)
    return RedirectResponse("/plan", status_code=303)


@router.post("/plan/archive")
def plan_archive(db: Session = Depends(get_session),
                 user: User = Depends(require_user)):
    training.archive_plan(db, user.id)
    return RedirectResponse("/plan", status_code=303)


# --------------------------- AI ---------------------------

@router.get("/ai", response_class=HTMLResponse)
def ai_page(request: Request, user: User = Depends(require_user)):
    from app.ai.provider import get_provider

    return templates.TemplateResponse(
        "ai.html",
        _ctx(request, "ai", user, provider_name=get_provider().name),
    )
