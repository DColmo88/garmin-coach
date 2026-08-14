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

    # AI
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "stub")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    CLAUDE_MODEL_LIGHT: str = os.getenv("CLAUDE_MODEL_LIGHT", "claude-haiku-4-5-20251001")
    CLAUDE_MODEL_HEAVY: str = os.getenv("CLAUDE_MODEL_HEAVY", "claude-sonnet-5")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    @property
    def ai_configured(self) -> bool:
        if self.AI_PROVIDER == "claude":
            return bool(self.ANTHROPIC_API_KEY)
        if self.AI_PROVIDER == "openai":
            return bool(self.OPENAI_API_KEY)
        return False


settings = Settings()
