"""Le due protezioni che si applicano a ogni richiesta: limite e intestazioni.

Vivono qui e non nei router perché sono trasversali: una regola che vale per
tutte le rotte scritta rotta per rotta è una regola che prima o poi qualcuno
dimentica su quella nuova.

**Perché in processo e non nel reverse proxy.** Il limite sta nel codice
Python, in un dizionario in memoria. Con un container solo e una cerchia
ristretta di utenti è sufficiente, non aggiunge dipendenze, e soprattutto è
verificabile dalla suite di test: un `rate_limit` in Caddy sarebbe corretto
quanto invisibile a `pytest`. Se un giorno i container diventassero due,
questo è il modulo da sostituire con Redis.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


# ============================================================================
# Limite di frequenza
# ============================================================================


@dataclass(frozen=True)
class Rule:
    """Quante richieste, in quanti secondi."""

    limit: int
    window_sec: int


# Le rotte protette e con quale severità. Sono tutte e sole quelle in cui una
# richiesta costa qualcosa di irrecuperabile:
#
# - `/login` e `/register`: ogni tentativo è un bcrypt, e senza limite l'unica
#   difesa contro chi prova password su un'email nota è la lunghezza della
#   password stessa;
# - `/connect/garmin`: ogni tentativo è un **login vero su Garmin**. Senza
#   limite questo server è un proxy per provare credenziali altrui contro
#   Garmin, con il nostro indirizzo IP a prendersi il blocco;
# - `/chat/send` e `/plan/generate`: costano soldi. Le quote le contano già,
#   ma la quota si verifica dopo aver costruito il contesto, e mille richieste
#   al secondo occupano comunque il processo;
# - il webhook Telegram: pubblico per forza, e il segreto nell'URL protegge
#   dall'uso, non dal martellamento.
RULES: dict[str, Rule] = {
    "POST /login": Rule(8, 300),
    "POST /register": Rule(5, 600),
    "POST /settings/password": Rule(8, 300),
    "POST /connect/garmin": Rule(5, 600),
    "POST /chat/send": Rule(30, 300),
    "POST /plan/generate": Rule(5, 3600),
    "POST /sync": Rule(10, 600),
}

# Il webhook ha il percorso variabile (contiene il segreto), quindi si
# riconosce per prefisso invece che per uguaglianza.
PREFIX_RULES: list[tuple[str, Rule]] = [
    ("POST /telegram/webhook/", Rule(60, 60)),
]


class _Counter:
    """Conteggi per chiave, con finestra scorrevole. Si ripulisce da solo."""

    def __init__(self) -> None:
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, rule: Rule, now: float) -> int | None:
        """Registra una richiesta. Restituisce i secondi d'attesa se è di troppo."""
        with self._lock:
            window_start = now - rule.window_sec
            recent = [t for t in self._hits.get(key, ()) if t > window_start]

            if len(recent) >= rule.limit:
                self._hits[key] = recent
                return max(1, int(recent[0] + rule.window_sec - now))

            recent.append(now)
            self._hits[key] = recent
            return None

    def prune(self, now: float, longest_window: int) -> None:
        """Butta via le chiavi che non hanno più colpi nella finestra più lunga."""
        with self._lock:
            cutoff = now - longest_window
            for key in [k for k, v in self._hits.items() if not v or v[-1] <= cutoff]:
                del self._hits[key]


def client_ip(request: Request) -> str:
    """L'indirizzo del chiamante. Due cautele, entrambe necessarie.

    Dietro Caddy `request.client.host` è sempre l'IP del container, quindi da
    solo conterebbe tutti gli utenti come se fossero uno. L'indirizzo vero
    arriva in `X-Forwarded-For`, ma quell'intestazione **la può scrivere
    chiunque**, e crederci senza condizioni vuol dire che per non avere alcun
    limite basta cambiarla a ogni richiesta. Quindi:

    - la si legge **solo in produzione**, dove sappiamo che davanti c'è il
      nostro proxy; in sviluppo l'app risponde direttamente e l'intestazione
      non ha nessuna autorità;
    - se ne prende **l'ultimo** valore e non il primo. Caddy *aggiunge* in
      coda, quindi l'ultimo l'ha scritto lui; quelli prima li può aver messi
      il client.
    """
    from app.config import settings

    if settings.is_production:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "sconosciuto"


# Il conteggio è un singolo oggetto di modulo e non un attributo del
# middleware, per una ragione pratica: `app` è costruita una volta sola e la
# suite di test la riusa per centinaia di richieste: senza un punto da cui
# azzerare i contatori, il ventesimo test si vedrebbe rifiutare il login per
# colpa del diciannovesimo. `reset()` è chiamato da una fixture in
# `tests/conftest.py`.
_COUNTER = _Counter()


def reset_rate_limits() -> None:
    """Dimentica tutti i conteggi. Serve ai test, non al funzionamento."""
    _COUNTER.__init__()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applica `RULES` per (indirizzo, rotta)."""

    def __init__(self, app, rules: dict[str, Rule] | None = None,
                 prefix_rules: list[tuple[str, Rule]] | None = None) -> None:
        super().__init__(app)
        self.rules = RULES if rules is None else rules
        self.prefix_rules = PREFIX_RULES if prefix_rules is None else prefix_rules
        self.counter = _COUNTER
        self._longest = max(
            [r.window_sec for r in self.rules.values()]
            + [r.window_sec for _, r in self.prefix_rules] + [60]
        )
        self._last_prune = 0.0

    def _rule_for(self, method: str, path: str) -> Rule | None:
        exact = self.rules.get(f"{method} {path}")
        if exact is not None:
            return exact
        for prefix, rule in self.prefix_rules:
            if f"{method} {path}".startswith(prefix):
                return rule
        return None

    async def dispatch(self, request: Request, call_next):
        rule = self._rule_for(request.method, request.url.path)
        if rule is None:
            return await call_next(request)

        now = time.monotonic()
        if now - self._last_prune > 300:
            self.counter.prune(now, self._longest)
            self._last_prune = now

        key = f"{client_ip(request)}|{request.method} {request.url.path}"
        retry_after = self.counter.hit(key, rule, now)
        if retry_after is None:
            return await call_next(request)

        logger.warning(
            "Limite di frequenza superato: %s %s da %s",
            request.method, request.url.path, client_ip(request),
        )
        message = (
            "Troppi tentativi. Riprova fra "
            f"{retry_after // 60 + 1} minuti."
            if retry_after > 60 else
            f"Troppi tentativi. Riprova fra {retry_after} secondi."
        )
        return JSONResponse(
            status_code=429,
            content={"error": message},
            headers={"Retry-After": str(retry_after)},
        )


# ============================================================================
# Intestazioni di sicurezza e Content-Security-Policy
# ============================================================================

# I `<script>` in fondo alle pagine dei grafici sono inline perché ricevono i
# dati da Jinja (`{{ chart | tojson }}`). Per poterli permettere senza aprire
# la porta a `'unsafe-inline'` — che renderebbe la CSP quasi inutile contro
# l'injection — ognuno porta il nonce di questa richiesta.
#
# `style-src` tollera l'inline, `script-src` no, e la differenza è voluta: una
# ventina di attributi `style=` nei template portano percentuali calcolate dai
# dati (la larghezza di una barra di progresso, la posizione di un ago), e non
# c'è modo di esprimerle in un foglio di stile statico. Un attributo `style`
# non esegue codice: il danno che resta possibile richiede comunque
# un'injection nel markup, che è ciò che `script-src` senza `unsafe-inline`
# impedisce di trasformare in esecuzione.
_CSP_TEMPLATE = (
    "default-src 'self'; "
    "script-src 'self' 'nonce-{nonce}'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)

# Le stesse che manda Caddy. Ripeterle qui non è ridondanza inutile: valgono
# anche in sviluppo e se un giorno il proxy davanti cambiasse.
_STATIC_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
}


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Genera il nonce, lo espone ai template e manda le intestazioni."""

    async def dispatch(self, request: Request, call_next) -> Response:
        from app.analysis import cache as analysis_cache

        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        # La memoria brevissima dei calcoli pesanti (vedi `app/analysis/cache.py`)
        # vive esattamente quanto una richiesta. Sta qui perché questo è
        # l'unico punto che avvolge ogni richiesta, non perché c'entri con la
        # sicurezza.
        analysis_cache.begin_request()
        try:
            response = await call_next(request)
        finally:
            analysis_cache.end_request()

        for header, value in _STATIC_HEADERS.items():
            response.headers.setdefault(header, value)
        response.headers.setdefault(
            "Content-Security-Policy", _CSP_TEMPLATE.format(nonce=nonce)
        )
        return response
