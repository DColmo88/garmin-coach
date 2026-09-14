"""Router delle pagine HTML della dashboard."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import goals
from app import providers
from app import queries as q
from app import sports
from app.ai.insights import Insight
from app.analysis import cache as analysis_cache
from app.analysis.records import personal_records
from app.analysis.zones import (
    ZONES,
    observed_bounds,
    read_distribution,
    zone_boundaries,
    zone_distribution,
)
from app.insights import (
    read_activities,
    read_body,
    read_fitness,
    read_health,
    read_sleep,
)
from app.ai.readiness import compute_readiness
from app.clock import today_for
from app.auth.session import require_user
from app.db.database import get_session
from app.db.models import DailyCoachCache, User
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
        # Dalla v3 l'account esiste prima della sorgente dei dati: fra la
        # registrazione e il collegamento c'è uno stato normale in cui l'app non
        # ha niente da leggere, e va detto una volta in cima invece di disegnare
        # grafici vuoti.
        "needs_connection": user.connection is None,
        "garmin_configured": user.connection is not None,
    }
    base.update(extra)
    return base


def _reading(db: Session, user: User, key: str, computed):
    """La lettura della pagina, con le parole dell'AI se ne sono state scritte.

    Il tono e le evidenze restano quelli calcolati in Python: l'AI cambia il
    titolo e il consiglio, non il giudizio.
    """
    from app.ai import readings

    row = db.scalar(
        select(DailyCoachCache).where(
            DailyCoachCache.user_id == user.id,
            DailyCoachCache.day == today_for(user),
        )
    )
    cached = (row.readings_json or {}).get(key) if row else None
    return readings.apply(computed, cached)


# Quanto indietro si può navigare col selettore dei giorni. Tre mesi coprono
# qualunque uso reale — «com'ero messo a inizio preparazione» — e mettono un
# fondo a una superficie che era illimitata: `?day=` accettava qualsiasi data,
# e ognuna era una riga di cache nuova più un giro di chiamate al modello.
MAX_DAYS_BACK = 90


def _requested_day(raw: str | None, user: User) -> date:
    """La data chiesta in `?day=`, riportata dentro i limiti consentiti.

    Il futuro non esiste e oltre `MAX_DAYS_BACK` non si va: una data fuori
    intervallo o scritta male diventa oggi, senza errore. È un selettore di
    navigazione, non un campo da validare con un messaggio.
    """
    today = today_for(user)
    if not raw:
        return today
    try:
        asked = date.fromisoformat(raw)
    except ValueError:
        return today
    if asked > today or asked < today - timedelta(days=MAX_DAYS_BACK):
        return today
    return asked


def _guard(db: Session, user: User, section: str) -> RedirectResponse | None:
    """Una sezione che questo utente non può avere non deve nemmeno rispondere.

    Chi usa Strava non ha sonno né recupero. La scelta è che quelle pagine per
    lui **non esistano** — non che esistano vuote con una spiegazione — quindi
    anche l'indirizzo digitato a mano rimanda al coach.
    """
    if section in providers.visible_sections(db, user):
        return None
    return RedirectResponse("/coach", status_code=303)


# --------------------------- Coach ---------------------------

@router.get("/coach", response_class=HTMLResponse)
def coach(request: Request, day: str | None = None, db: Session = Depends(get_session),
          user: User = Depends(require_user)):

    target_date = _requested_day(day, user)

    snap = q.coach_snapshot(db, user.id, target_date)
    readiness = compute_readiness(snap)
    goal = goals.active_goal(db, user.id)

    # Il messaggio del coach arriva dalla cache del giorno: aprire la pagina
    # dieci volte non deve costare dieci chiamate all'AI.
    cached = refresh_daily_cache(db, user, target_date)
    # `.get` e non `[...]`: le righe di cache scritte prima che gli insight
    # perdessero l'emoji hanno una chiave in più, e non devono far esplodere
    # la pagina il giorno del deploy.
    insights = [
        Insight(i.get("title", ""), i.get("text", ""), i.get("color", "green"), 0)
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

    # Un indicatore senza dato non si mostra vuoto: si toglie. Tre cerchi a
    # zero direbbero «sei a zero», che è un'altra cosa da «non lo misuriamo».
    mini = [
        m for m in (
            {"value": _r(snap.get("sleep_score")), "label": "Sonno"},
            {"value": _r(snap.get("body_battery_high")), "label": "Battery"},
            {"value": _r(snap.get("vo2max_latest")), "label": "VO₂max", "vmax": 70},
        )
        if m["value"] is not None
    ]
    from app import gamification

    progress = gamification.progress_of(db, user)
    in_range = sum(1 for f in readiness.breakdown if f.color == "green")

    # Chi usa Strava non ha sonno né HRV: la prontezza pesa quei due per 0.55 e
    # senza di loro non arriva alla soglia di fondatezza. Invece di lasciare un
    # buco dov'era il numero più grande della pagina, il titolo diventa la
    # forma — che per Strava è pienamente calcolabile, perché la calcoliamo noi
    # dalle attività. L'«oggi» di chi usa Strava parla di allenamento invece
    # che di recupero: più piccolo, ma intero.
    has_readiness = "readiness" in providers.visible_sections(db, user)
    load = None if has_readiness else analysis_cache.load_summary(db, user)

    # L'allenamento di oggi: se c'è un piano attivo è **quello che il piano
    # prevede**, già adattato alla prontezza da `today_session`. Il consiglio
    # generico dell'AI serve a chi un piano non ce l'ha — mostrarlo a chi ce
    # l'ha significherebbe dargli due indicazioni diverse per la stessa
    # giornata, e la seconda che ignora il programma che sta seguendo.
    plan = training.get_active_plan(db, user.id)
    planned = training.today_session(plan, readiness.score, target_date) if plan else None
    
    # Il soggettivo: il check-in di oggi e le sedute che aspettano lo sforzo
    # percepito. Si chiedono solo sulla giornata corrente — non si compila il
    # passato — e sono l'unico dato dell'app che non arriva da un apparecchio.
    from app.routers import subjective

    is_today = target_date == today_for(user)
    checkin = subjective.todays_checkin(db, user) if is_today else None
    awaiting_effort = (
        subjective.activities_awaiting_effort(db, user) if is_today else []
    )
    prev_day = (target_date - timedelta(days=1)).isoformat()
    next_day = (target_date + timedelta(days=1)).isoformat()
    
    return templates.TemplateResponse(
        "coach.html",
        _ctx(request, "coach", user, snap=snap, readiness=readiness, insights=insights,
             coaching=coaching, mini=mini, in_range=in_range,
             goal=goal, days_left=goals.days_to_target(goal) if goal else None,
             progress=progress, has_readiness=has_readiness, load=load,
             planned=planned, has_plan=plan is not None,
             total_metrics=len(readiness.breakdown),
             target_date=target_date, prev_day=prev_day, next_day=next_day,
             is_today=is_today, checkin=checkin, awaiting_effort=awaiting_effort),
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

    # Il conteggio è per nome leggibile, non per codice Garmin: nella legenda
    # del grafico «indoor_cycling» diventa «Bici indoor».
    by_type: dict[str, int] = {}
    raw_types: set[str | None] = set()
    for a in rows:
        raw_types.add(a.activity_type)
        label = sports.label_of(a)
        by_type[label] = by_type.get(label, 0) + 1

    chart = {"type_labels": list(by_type.keys()), "type_counts": list(by_type.values())}
    zones = zone_distribution(rows)
    return templates.TemplateResponse(
        "activities.html",
        _ctx(request, "activities", user, activities=rows, chart=chart,
             zones=zones, reading=read_activities(rows, zones),
             # L'intestazione della colonna cambia se lo storico mescola sport
             # che si misurano a ritmo con altri che si misurano a velocità.
             speed_header=sports.speed_header(raw_types)),
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
    if (elsewhere := _guard(db, user, "sleep")) is not None:
        return elsewhere
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
             reading=_reading(db, user, "sleep", read_sleep(rows))),
    )


# --------------------------- Salute ---------------------------

@router.get("/health", response_class=HTMLResponse)
def health(request: Request, db: Session = Depends(get_session),
           user: User = Depends(require_user)):
    if (elsewhere := _guard(db, user, "health")) is not None:
        return elsewhere
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
             reading=_reading(db, user, "health", read_health(rows))),
    )


# --------------------------- Corpo ---------------------------

@router.get("/body", response_class=HTMLResponse)
def body(request: Request, db: Session = Depends(get_session),
         user: User = Depends(require_user)):
    if (elsewhere := _guard(db, user, "body")) is not None:
        return elsewhere
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
             reading=_reading(db, user, "body", read_body(rows))),
    )


# --------------------------- Forma e carico ---------------------------

@router.get("/performance")
def performance_moved():
    """La vecchia pagina Performance è confluita in Forma e carico."""
    return RedirectResponse("/fitness", status_code=301)


@router.get("/fitness", response_class=HTMLResponse)
def fitness(request: Request, db: Session = Depends(get_session),
            user: User = Depends(require_user)):
    """Fitness, fatica, forma: il ciclo carico-recupero in un posto solo.

    I numeri del carico sono calcolati dalle attività (`app.analysis`), non
    letti da Garmin: `training_load` per molti account resta vuoto.
    """
    rows = q.training_series(db, user.id, 90)
    load = analysis_cache.load_summary(db, user)

    chart = {
        "labels": q.labels(rows),
        "vo2max": q.values(rows, "vo2max"),
        "vo2max_cycling": q.values(rows, "vo2max_cycling"),
        "hrv": q.values(rows, "hrv_weekly_avg"),
        "readiness": q.values(rows, "readiness_score"),
        "pmc": load.chart(90),
        "zones": {
            "labels": [f"Z{z['n']} · {z['name']}" for z in ZONES],
            "minutes": [round(s / 60) for s in load.zones.seconds],
            "pct": [round(p, 1) for p in load.zones.percentages],
        },
    }

    # I confini delle zone: quelli dichiarati dall'orologio se ci sono, e si
    # dice quale delle due cose è. Il grafico conta i secondi con le zone del
    # dispositivo; ricalcolarli qui come percentuali della FC massima poteva
    # dare una legenda che non corrispondeva alle barre.
    boundaries, bounds_source = zone_boundaries(
        load.profile.hr_max,
        observed_bounds(q.recent_activities(db, user.id, 50)),
    )

    snapshot = None
    error = None
    try:
        snapshot = service.get_performance_snapshot(user)
    except GarminClientError as exc:
        error = str(exc)
    except Exception as exc:  # noqa: BLE001
        error = f"Dati live non disponibili: {exc}"

    return templates.TemplateResponse(
        "fitness.html",
        _ctx(request, "fitness", user, rows=list(reversed(rows)), chart=chart,
             load=load, zones=ZONES,
             zone_reading=read_distribution(load.zones),
             boundaries=boundaries, bounds_source=bounds_source,
             records=personal_records(q.recent_activities(db, user.id, 500),
                                      sports.primary_sport(user)),
             snapshot=snapshot, error=error,
             reading=_reading(db, user, "fitness", read_fitness(rows, load))),
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
             today_name=training.WEEKDAYS[today_for(user).weekday()],
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


# --------------------------- Rotte ritirate ---------------------------

@router.get("/ai")
def ai_moved():
    """La pagina AI della v1 non esiste più.

    Era un banco di prova con un bottone che chiamava `/ai/plan`, endpoint
    rimosso quando i provider sono diventati reali. Il suo posto lo hanno preso
    la chat (`/chat`) e la generazione del piano (`/plan`).
    """
    return RedirectResponse("/chat", status_code=301)
