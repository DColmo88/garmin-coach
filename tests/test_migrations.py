"""La migrazione Alembic deve ricostruire l'intero schema su un DB vuoto.

È il test che ci protegge dal caso peggiore in produzione: modelli aggiornati
ma migrazione dimenticata.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

PROJECT_ROOT = Path(__file__).resolve().parent.parent

EXPECTED_TABLES = {
    "users",
    "invite_codes",
    "push_subscriptions",
    "activities",
    "sleep_records",
    "training_metrics",
    "daily_wellness",
    "body_composition",
    "user_goals",
    "training_plans",
    "daily_coach_cache",
    "gamification_state",
    "chat_conversations",
    "chat_messages",
    "ai_usage_log",
    "notification_log",
}


def test_alembic_upgrade_head_builds_full_schema(tmp_path):
    url = f"sqlite:///{tmp_path / 'mig.db'}"
    env = {**os.environ, "DATABASE_URL": url}

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    tables = set(inspect(create_engine(url)).get_table_names())
    assert EXPECTED_TABLES <= tables, EXPECTED_TABLES - tables


def test_models_and_migration_agree(tmp_path):
    """Dopo l'upgrade, l'autogenerate non deve trovare differenze."""
    url = f"sqlite:///{tmp_path / 'check.db'}"
    env = {**os.environ, "DATABASE_URL": url}

    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True, check=True,
    )
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "check"],
        cwd=PROJECT_ROOT, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, f"schema disallineato:\n{result.stdout}{result.stderr}"
