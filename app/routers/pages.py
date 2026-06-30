"""Router delle pagine HTML della dashboard."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import queries as q
from app.ai.insights import top_insights
from app.ai.provider import get_provider
from app.ai.readiness import compute_readiness
from app.config import settings
from app.db.database import get_session
from app.garmin import service
from app.garmin.client import GarminClientError
from app.templating import templates

router = APIRouter()


def _ctx(request: Request, active: str, **extra) -> dict:
    base = {
        "request": request,
        "active": active,
        "garmin_configured": settings.garmin_configured,
    }
    base.update(extra)
    return base


# --------------------------- Coach ---------------------------

@router.get("/coach", response_class=HTMLResponse)
def coach(request: Request, db: Session = Depends(get_session)):
    snap = q.coach_snapshot(db)
    readiness = compute_readiness(snap)
    insights = top_insights(snap, 3)
    coaching = get_provider().coach(snap, readiness)
    mini = [
        {"value": snap.get("sleep_score"), "label": "Sonno"},
        {"value": snap.get("body_battery_high"), "label": "Battery"},
        {"value": round(snap["vo2max_latest"]) if snap.get("vo2max_latest") else None,
         "label": "VO₂max", "vmax": 70},
    ]
    in_range = sum(1 for f in readiness.breakdown if f.color == "green")
    return templates.TemplateResponse(
        "coach.html",
        _ctx(request, "coach", snap=snap, readiness=readiness, insights=insights,
             coaching=coaching, mini=mini, in_range=in_range,
             total_metrics=len(readiness.breakdown)),
    )


# --------------------------- Panoramica ---------------------------

@router.get("/", response_class=HTMLResponse)
def overview(request: Request, db: Session = Depends(get_session)):
    wellness = q.wellness_series(db, 28)
    sleep = q.sleep_series(db, 28)
    training = q.training_series(db, 28)
    activities = q.recent_activities(db, 5)

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
        _ctx(request, "overview", chart=chart, kpis=kpis, activities=activities,
             has_data=bool(wellness or training)),
    )


# --------------------------- Attività ---------------------------

@router.get("/activities", response_class=HTMLResponse)
def activities(request: Request, db: Session = Depends(get_session)):
    rows = q.recent_activities(db, 50)
    by_type: dict[str, int] = {}
    for a in rows:
        by_type[a.activity_type or "altro"] = by_type.get(a.activity_type or "altro", 0) + 1
    chart = {"type_labels": list(by_type.keys()), "type_counts": list(by_type.values())}
    return templates.TemplateResponse(
        "activities.html",
        _ctx(request, "activities", activities=rows, chart=chart),
    )


@router.get("/activities/{activity_id}", response_class=HTMLResponse)
def activity_detail(activity_id: int, request: Request, db: Session = Depends(get_session)):
    activity = q.get_activity(db, activity_id)
    detail = None
    error = None
    try:
        detail = service.get_activity_full(activity_id)
    except GarminClientError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        error = f"Impossibile caricare i dettagli live: {exc}"
    return templates.TemplateResponse(
        "activity_detail.html",
        _ctx(request, "activities", activity=activity, detail=detail, error=error,
             activity_id=activity_id),
    )


# --------------------------- Sonno ---------------------------

@router.get("/sleep", response_class=HTMLResponse)
def sleep(request: Request, db: Session = Depends(get_session)):
    rows = q.sleep_series(db, 28)
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
        _ctx(request, "sleep", rows=list(reversed(rows)), chart=chart),
    )


# --------------------------- Salute ---------------------------

@router.get("/health", response_class=HTMLResponse)
def health(request: Request, db: Session = Depends(get_session)):
    rows = q.wellness_series(db, 28)
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
        _ctx(request, "health", rows=list(reversed(rows)), chart=chart),
    )


# --------------------------- Corpo ---------------------------

@router.get("/body", response_class=HTMLResponse)
def body(request: Request, db: Session = Depends(get_session)):
    rows = q.body_series(db, 90)
    chart = {
        "labels": q.labels(rows),
        "weight": [(v or 0) / 1000 if v else None for v in q.values(rows, "weight_g")],
        "body_fat": q.values(rows, "body_fat_pct"),
        "muscle": [(v or 0) / 1000 if v else None for v in q.values(rows, "muscle_mass_g")],
        "bmi": q.values(rows, "bmi"),
    }
    return templates.TemplateResponse(
        "body.html",
        _ctx(request, "body", rows=list(reversed(rows)), chart=chart),
    )


# --------------------------- Performance ---------------------------

@router.get("/performance", response_class=HTMLResponse)
def performance(request: Request, db: Session = Depends(get_session)):
    rows = q.training_series(db, 28)
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
    if settings.garmin_configured:
        try:
            snapshot = service.get_performance_snapshot()
        except GarminClientError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001
            error = f"Dati live non disponibili: {exc}"
    return templates.TemplateResponse(
        "performance.html",
        _ctx(request, "performance", rows=list(reversed(rows)), chart=chart,
             snapshot=snapshot, error=error),
    )


# --------------------------- Dispositivi & Gear ---------------------------

@router.get("/devices", response_class=HTMLResponse)
def devices(request: Request):
    data = {"devices": [], "gear": [], "badges": {}}
    error = None
    if settings.garmin_configured:
        try:
            data["devices"] = service.get_devices()
            data["gear"] = service.get_gear_overview()
            data["badges"] = service.get_badges()
        except GarminClientError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001
            error = f"Dati live non disponibili: {exc}"
    return templates.TemplateResponse(
        "devices.html",
        _ctx(request, "devices", data=data, error=error),
    )


# --------------------------- AI ---------------------------

@router.get("/ai", response_class=HTMLResponse)
def ai_page(request: Request):
    from app.ai.provider import get_provider

    return templates.TemplateResponse(
        "ai.html",
        _ctx(request, "ai", provider_name=get_provider().name),
    )
