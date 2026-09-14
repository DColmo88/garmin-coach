"""Applicazione FastAPI: dashboard multi-pagina + endpoint API.

Avvio:  uvicorn app.main:app --reload
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from app.ai.context import build_ai_context
from app.auth.session import LoginRequired, require_user
from app.config import settings
from app.db.database import get_session, init_db
from app.db.models import User
from app.pipeline import run_for_user
from app.routers import admin as admin_router
from app.routers import auth as auth_router
from app.routers import chat as chat_router
from app.routers import connect as connect_router
from app.routers import pages
from app.routers import settings as settings_router
from app.routers import subjective as subjective_router
from app.scheduler import next_run_time, start_scheduler, stop_scheduler
from app.security import RateLimitMiddleware, SecurityHeadersMiddleware

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Avvio e spegnimento.

    La prima cosa è `settings.validate()`: in produzione l'app non deve partire
    con `SESSION_SECRET` mancante o ancora quello di sviluppo. Quel default è
    pubblico — sta nel codice — e con quello attivo chiunque può firmarsi un
    cookie per l'utente 1, che è l'amministratore. Meglio un container che non
    parte e lo dice, di uno che parte aperto.
    """
    settings.validate()
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Garmin Coach", lifespan=lifespan)

# L'ordine conta: Starlette esegue i middleware dall'ultimo aggiunto al primo,
# quindi il limite di frequenza gira **prima** di tutto il resto — chi sta
# martellando il login non deve nemmeno arrivare a toccare il database.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware)

app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(admin_router.router)
app.include_router(auth_router.router)
app.include_router(chat_router.router)
app.include_router(connect_router.router)
app.include_router(settings_router.router)
app.include_router(subjective_router.router)
app.include_router(pages.router)


@app.exception_handler(LoginRequired)
def handle_login_required(request: Request, exc: LoginRequired):
    """Chi non ha una sessione valida finisce sulla pagina di accesso."""
    return RedirectResponse("/login", status_code=303)


@app.exception_handler(admin_router.NotAdmin)
def handle_not_admin(request: Request, exc: admin_router.NotAdmin):
    """Chi non e' amministratore non deve nemmeno sapere che /admin esiste."""
    return RedirectResponse("/coach", status_code=303)


# --------------------------- Sync ---------------------------

@app.post("/sync")
def trigger_sync(
    db: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Sync manuale: stessa pipeline di quella notturna, eseguita subito."""
    result = run_for_user(db, user)
    if not result.ok:
        # Gli errori della pipeline portano dentro il testo delle eccezioni di
        # `garminconnect` e `stravalib`: indirizzi, codici di risposta, a volte
        # pezzi del corpo. Vanno nel log, non nel browser.
        logger.warning("Sync manuale fallita per utente %s: %s", user.id, result.errors)
        return JSONResponse(
            status_code=400,
            content={"error": "Sincronizzazione non riuscita. Controlla la "
                              "sorgente dati in Impostazioni e riprova."},
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


# `POST /ai/plan` non esiste più. Era il residuo della pagina `/ai` della v1,
# ritirata da tempo, e generava un piano — l'operazione AI più costosa che
# l'app abbia — **senza controllare la quota e senza registrare il consumo**.
# La generazione dei piani passa da `/plan/generate`, che fa entrambe le cose.


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
