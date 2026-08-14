"""Applicazione FastAPI: dashboard multi-pagina + endpoint API.

Avvio:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.ai.context import build_ai_context
from app.ai.provider import get_provider
from app.auth.session import LoginRequired, require_user
from app.db.database import get_session, init_db
from app.db.models import User
from app.pipeline import run_for_user
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import chat as chat_router
from app.routers import pages
from app.routers import settings as settings_router
from app.scheduler import next_run_time, start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Garmin Coach")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(admin_router.router)
app.include_router(auth_router.router)
app.include_router(chat_router.router)
app.include_router(settings_router.router)
app.include_router(pages.router)


@app.exception_handler(LoginRequired)
def handle_login_required(request: Request, exc: LoginRequired):
    """Chi non ha una sessione valida finisce sulla pagina di accesso."""
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(admin_router.NotAdmin)
def handle_not_admin(request: Request, exc: admin_router.NotAdmin):
    """Chi non e' amministratore non deve nemmeno sapere che /admin esiste."""
    return RedirectResponse("/coach", status_code=303)


@app.on_event("startup")
def on_startup() -> None:
    init_db()
    start_scheduler()


@app.on_event("shutdown")
def on_shutdown() -> None:
    stop_scheduler()


# --------------------------- Sync ---------------------------

@app.post("/sync")
def trigger_sync(
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Sync manuale: stessa pipeline di quella notturna, eseguita subito."""
    result = run_for_user(db, user)
    if not result.ok:
        return JSONResponse(
            status_code=400, content={"error": "; ".join(result.errors)}
        )
    return {
        "status": "ok",
        "synced": result.synced,
        "readiness": result.readiness,
        "insights": result.insights,
    }


# --------------------------- API JSON ---------------------------

@app.get("/api/context")
def api_context(
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Contesto strutturato dei dati (utile per debug e per l'AI)."""
    return build_ai_context(db, user.id)


# --------------------------- AI ---------------------------

@app.post("/ai/plan")
def ai_plan(
    goal: str = Form("Migliorare la forma generale"),
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Genera un piano/insight (provider scelto da AI_PROVIDER)."""
    context = build_ai_context(db, user.id)
    provider = get_provider()
    plan = provider.generate_training_plan(context, goal)
    return {"provider": provider.name, "goal": goal, "plan": plan}


@app.get("/api/sync-status")
def api_sync_status(user: User = Depends(require_user)):
    """Stato della sync per l'utente corrente (usato dalla UI)."""
    nxt = next_run_time()
    return {
        "last_sync_at": user.last_sync_at.isoformat() if user.last_sync_at else None,
        "sync_failures": user.sync_failures,
        "next_scheduled_sync": nxt.isoformat() if nxt else None,
    }


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
