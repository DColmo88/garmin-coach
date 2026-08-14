"""Configurazione centrale: carica le variabili dal file .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Carica .env dalla root del progetto
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    """Impostazioni dell'applicazione, lette dall'ambiente.

    Le credenziali Garmin non stanno più qui: dalla v2 sono per-utente,
    cifrate nel DB (vedi app/auth/security.py).
    """

    GARMIN_TOKENSTORE: str = os.getenv("GARMIN_TOKENSTORE", "./data/garmin_tokens")

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data/garmin_connector.db")

    # Auth multi-utente
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "dev-only-change-me")
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
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    # Indirizzo pubblico dell'app: finisce nei link delle notifiche
    PUBLIC_BASE_URL: str = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")

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
    def ai_configured(self) -> bool:
        if self.AI_PROVIDER == "claude":
            return bool(self.ANTHROPIC_API_KEY)
        if self.AI_PROVIDER == "openai":
            return bool(self.OPENAI_API_KEY)
        return False


settings = Settings()
