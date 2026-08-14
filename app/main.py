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
from app.garmin import service
from app.garmin.client import GarminClientError
from app.garmin.sync import sync_all
from app.routers import auth as auth_router
from app.routers import pages

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="Garmin Coach")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(auth_router.router)
app.include_router(pages.router)


@app.exception_handler(LoginRequired)
def handle_login_required(request: Request, exc: LoginRequired):
    """Chi non ha una sessione valida finisce sulla pagina di accesso."""
    return RedirectResponse("/login", status_code=303)


@app.on_event("startup")
def on_startup() -> None:
    init_db()


# --------------------------- Sync ---------------------------

@app.post("/sync")
def trigger_sync(
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Scarica i dati freschi da Garmin e li salva nel DB."""
    try:
        result = sync_all(db, user)
        service.clear_cache(user.id)
    except GarminClientError as exc:
        return JSONResponse(status_code=400, content={"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        logger.exception("Errore durante la sync")
        return JSONResponse(status_code=500, content={"error": str(exc)})

    from datetime import datetime

    user.last_sync_at = datetime.utcnow()
    user.sync_failures = 0
    db.commit()
    return {"status": "ok", "synced": result}


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


@app.get("/healthz")
def healthz():
    return {"status": "ok"}
