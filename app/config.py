"""Configurazione centrale: carica le variabili dal file .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Carica .env dalla root del progetto
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Il valore che `SESSION_SECRET` assume quando nessuno l'ha impostato. Sta qui
# come costante perché serve in due posti: come default, e come cosa da
# rifiutare all'avvio in produzione.
DEV_SESSION_SECRET = "dev-only-change-me"


class ConfigError(RuntimeError):
    """La configurazione non è adatta all'ambiente in cui l'app sta partendo."""


class Settings:
    """Impostazioni dell'applicazione, lette dall'ambiente.

    Le credenziali Garmin non stanno più qui: dalla v2 sono per-utente,
    cifrate nel DB (vedi app/auth/security.py).
    """

    GARMIN_TOKENSTORE: str = os.getenv("GARMIN_TOKENSTORE", "./data/garmin_tokens")

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data/garmin_connector.db")

    # Auth multi-utente
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", DEV_SESSION_SECRET)
    FERNET_KEY: str = os.getenv("FERNET_KEY", "")

    # Scheduler (sync automatica giornaliera)
    SCHEDULER_ENABLED: bool = os.getenv("SCHEDULER_ENABLED", "true").lower() == "true"
    SCHEDULER_TIMEZONE: str = os.getenv("SCHEDULER_TIMEZONE", "Europe/Rome")
    SYNC_HOUR: int = int(os.getenv("SYNC_HOUR", "6"))
    SYNC_MINUTE: int = int(os.getenv("SYNC_MINUTE", "30"))
    SYNC_STAGGER_MINUTES: int = int(os.getenv("SYNC_STAGGER_MINUTES", "3"))

    # AI
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "stub")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL_LIGHT: str = os.getenv("CLAUDE_MODEL_LIGHT", "claude-haiku-4-5")
    CLAUDE_MODEL_HEAVY: str = os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-5")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    # Due modelli come per Claude: il coaching giornaliero è un testo di tre
    # frasi su dati già decisi e non ha bisogno del modello grande; la chat e i
    # piani sì, perché è lì che serve capire davvero il quadro.
    OPENAI_MODEL_LIGHT: str = os.getenv("OPENAI_MODEL_LIGHT", "gpt-4o-mini")
    OPENAI_MODEL_HEAVY: str = os.getenv("OPENAI_MODEL_HEAVY", "gpt-4o")
    # Retrocompatibilità: chi aveva OPENAI_MODEL nel .env se lo ritrova come
    # modello leggero invece di vederlo ignorato in silenzio.
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "")

    # Indirizzo pubblico dell'app: finisce nei link delle notifiche
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

    # Strava — app OAuth registrata su https://www.strava.com/settings/api.
    # Sono credenziali dell'applicazione, non dell'utente: valgono per tutti
    # quelli che collegano Strava. I token dei singoli stanno cifrati nel DB.
    STRAVA_CLIENT_ID: str = os.getenv("STRAVA_CLIENT_ID", "")
    STRAVA_CLIENT_SECRET: str = os.getenv("STRAVA_CLIENT_SECRET", "")

    # Notifiche — email (SMTP)
    SMTP_HOST: str = os.getenv("SMTP_HOST", "")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USER: str = os.getenv("SMTP_USER", "")
    SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
    SMTP_FROM: str = os.getenv("SMTP_FROM", "")
    SMTP_USE_TLS: bool = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
    SMTP_USE_SSL: bool = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

    # Notifiche — Telegram
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

    @property
    def telegram_webhook_secret(self) -> str:
        """Segreto nell'URL del webhook, derivato dal token del bot.

        Deterministico (non serve un'altra variabile da configurare) ma non
        risalibile al token, così l'URL può stare nei log di Telegram.
        """
        import hashlib

        if not self.TELEGRAM_BOT_TOKEN:
            return ""
        seed = f"{self.TELEGRAM_BOT_TOKEN}{self.SESSION_SECRET}".encode()
        return hashlib.sha256(seed).hexdigest()[:32]

    # Notifiche — Web Push (PWA)
    VAPID_PUBLIC_KEY: str = os.getenv("VAPID_PUBLIC_KEY", "")
    VAPID_PRIVATE_KEY: str = os.getenv("VAPID_PRIVATE_KEY", "")
    VAPID_CONTACT_EMAIL: str = os.getenv("VAPID_CONTACT_EMAIL", "noreply@garmin-coach.local")

    @property
    def is_production(self) -> bool:
        """Vero quando l'app è raggiungibile da fuori, in HTTPS.

        Si deduce da `PUBLIC_BASE_URL` invece di chiedere una variabile in più:
        un indirizzo pubblico in https **è** la definizione operativa di
        «questa non è la mia macchina». Il vantaggio pratico è che i `.env.prod`
        già scritti restano validi — un flag nuovo e obbligatorio avrebbe
        impedito l'avvio al primo deploy dopo questa modifica, che è il modo
        peggiore di introdurre un controllo di sicurezza.
        """
        return self.PUBLIC_BASE_URL.startswith("https://")

    def validate(self) -> None:
        """Controlla che i segreti esistano davvero. Solleva in produzione.

        Il default di `SESSION_SECRET` è pubblico: sta nel codice e nel
        `.env.example`. Con quello attivo chiunque può firmarsi un cookie per
        l'utente 1, che è l'amministratore. Finora l'app partiva lo stesso e
        non lo diceva a nessuno.

        In sviluppo resta un avviso: bloccare `uvicorn --reload` perché manca
        una chiave sarebbe una seccatura senza guadagno, lì non c'è niente da
        proteggere.
        """
        import logging

        problems: list[str] = []
        if not self.SESSION_SECRET or self.SESSION_SECRET == DEV_SESSION_SECRET:
            problems.append(
                "SESSION_SECRET manca o è ancora quello di sviluppo — genera con: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        if not self.FERNET_KEY:
            problems.append(
                "FERNET_KEY manca — genera con: python -c \"from cryptography.fernet "
                'import Fernet; print(Fernet.generate_key().decode())"'
            )

        if not problems:
            return
        if self.is_production:
            raise ConfigError(
                "Configurazione non adatta alla produzione:\n- " + "\n- ".join(problems)
            )
        logging.getLogger(__name__).warning(
            "Segreti di sviluppo in uso: %s", "; ".join(problems)
        )

    @property
    def strava_configured(self) -> bool:
        """True se l'app può offrire Strava fra le sorgenti.

        Senza le due credenziali la scheda Strava non compare proprio: meglio
        non mostrare una scelta che poi non si può completare.
        """
        return bool(self.STRAVA_CLIENT_ID and self.STRAVA_CLIENT_SECRET)

    @property
    def ai_configured(self) -> bool:
        if self.AI_PROVIDER == "claude":
            return bool(self.ANTHROPIC_API_KEY)
        if self.AI_PROVIDER == "openai":
            return bool(self.OPENAI_API_KEY)
        return False


settings = Settings()
