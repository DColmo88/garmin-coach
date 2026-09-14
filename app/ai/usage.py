"""Consumo AI: registrazione, quote e stima dei costi.

Il budget del progetto è di pochi euro al mese, quindi ogni chiamata viene
registrata e ogni utente ha un tetto giornaliero. Il controllo è volutamente
semplice: un conteggio sul giorno corrente, nessuna finestra scorrevole.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import today_for
from app.db.models import AIUsageLog, User

logger = logging.getLogger(__name__)

Kind = Literal["chat", "coach", "plan"]

# Prezzi in dollari per milione di token (aggiornati 2026-08).
# Servono solo a stimare la spesa nella pagina admin: nessuna logica dipende
# da questi numeri, quindi un listino leggermente datato non rompe nulla.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}
_DEFAULT_PRICE = (1.00, 5.00)

# Il coaching giornaliero dovrebbe costare una chiamata al giorno: la cache lo
# garantisce finché il giorno è uno solo. Il tetto esiste perché quella
# garanzia è saltata una volta — `/coach?day=` creava una riga di cache, e
# quindi tre chiamate al modello, per ogni data digitata — e perché un limite
# esplicito è più facile da verificare di un invariante implicito. Quattro e
# non uno: un errore del provider deve poter essere ritentato.
MAX_COACH_CALLS_DAILY = 4


def _today(db: Session, user_id: int) -> date:
    """Che giorno è per questo utente.

    Le quote si azzerano a mezzanotte, e «mezzanotte» è quella di chi usa
    l'app. Con il server su UTC e l'atleta a Roma la quota della chat si
    azzerava alle due del mattino, mentre l'interfaccia prometteva mezzanotte.
    """
    user = db.get(User, user_id)
    return today_for(user) if user is not None else date.today()


class QuotaExceeded(Exception):
    """L'utente ha esaurito la quota per questo tipo di chiamata."""

    def __init__(self, kind: Kind, used: int, limit: int) -> None:
        self.kind = kind
        self.used = used
        self.limit = limit
        super().__init__(f"Quota {kind} esaurita: {used}/{limit}")


@dataclass
class UsageSummary:
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: float


def record(
    db: Session,
    user_id: int,
    kind: Kind,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> AIUsageLog:
    """Registra una chiamata AI."""
    row = AIUsageLog(
        user_id=user_id,
        day=_today(db, user_id),
        kind=kind,
        model=model,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
    )
    db.add(row)
    db.commit()
    return row


def count_today(db: Session, user_id: int, kind: Kind) -> int:
    """Quante chiamate di questo tipo l'utente ha fatto oggi."""
    return db.scalar(
        select(func.count(AIUsageLog.id)).where(
            AIUsageLog.user_id == user_id,
            AIUsageLog.kind == kind,
            AIUsageLog.day == _today(db, user_id),
        )
    ) or 0


def count_this_month(db: Session, user_id: int, kind: Kind) -> int:
    first_of_month = _today(db, user_id).replace(day=1)
    return db.scalar(
        select(func.count(AIUsageLog.id)).where(
            AIUsageLog.user_id == user_id,
            AIUsageLog.kind == kind,
            AIUsageLog.day >= first_of_month,
        )
    ) or 0


def check_quota(db: Session, user: User, kind: Kind) -> None:
    """Solleva QuotaExceeded se l'utente ha esaurito il suo tetto."""
    if kind == "chat":
        limit = user.ai_quota_chat_daily
        used = count_today(db, user.id, "chat")
    elif kind == "plan":
        limit = user.ai_quota_plans_monthly
        used = count_this_month(db, user.id, "plan")
    else:  # coach: la cache ne prevede una al giorno, il tetto la impone
        limit = MAX_COACH_CALLS_DAILY
        used = count_today(db, user.id, "coach")

    if used >= limit:
        raise QuotaExceeded(kind, used, limit)


def remaining(db: Session, user: User, kind: Kind) -> int:
    if kind == "chat":
        return max(0, user.ai_quota_chat_daily - count_today(db, user.id, "chat"))
    if kind == "plan":
        return max(0, user.ai_quota_plans_monthly - count_this_month(db, user.id, "plan"))
    return max(0, MAX_COACH_CALLS_DAILY - count_today(db, user.id, "coach"))


def estimate_cost(model: str | None, tokens_in: int, tokens_out: int) -> float:
    """Costo stimato in dollari di una singola chiamata."""
    price_in, price_out = PRICES_PER_MTOK.get(model or "", _DEFAULT_PRICE)
    return (tokens_in * price_in + tokens_out * price_out) / 1_000_000


def summary(db: Session, user_id: int | None = None, since: date | None = None) -> UsageSummary:
    """Riepilogo del consumo, per un utente o per tutti."""
    query = select(AIUsageLog)
    if user_id is not None:
        query = query.where(AIUsageLog.user_id == user_id)
    if since is not None:
        query = query.where(AIUsageLog.day >= since)

    rows = list(db.scalars(query).all())
    return UsageSummary(
        calls=len(rows),
        tokens_in=sum(r.tokens_in for r in rows),
        tokens_out=sum(r.tokens_out for r in rows),
        cost_usd=sum(estimate_cost(r.model, r.tokens_in, r.tokens_out) for r in rows),
    )
