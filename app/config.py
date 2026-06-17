"""Configurazione centrale: carica le variabili dal file .env."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Carica .env dalla root del progetto
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


class Settings:
    """Impostazioni dell'applicazione, lette dall'ambiente."""

    GARMIN_EMAIL: str = os.getenv("GARMIN_EMAIL", "")
    GARMIN_PASSWORD: str = os.getenv("GARMIN_PASSWORD", "")
    GARMIN_TOKENSTORE: str = os.getenv("GARMIN_TOKENSTORE", "./data/garmin_tokens")

    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./data/garmin_connector.db")

    # AI (predisposto per il futuro)
    AI_PROVIDER: str = os.getenv("AI_PROVIDER", "stub")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")

    @property
    def garmin_configured(self) -> bool:
        return bool(self.GARMIN_EMAIL and self.GARMIN_PASSWORD)


settings = Settings()
