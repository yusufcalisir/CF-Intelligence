"""Programmatic Alembic Migration Manager.

Provides application startup auto-migration, schema version querying,
and programmatic upgrade/downgrade utilities for single-tenant and multi-tenant setups.
"""

from __future__ import annotations

import logging
from pathlib import Path

from alembic.config import Config

from alembic import command
from app.config import get_settings

logger = logging.getLogger(__name__)

# Path to alembic.ini relative to backend directory
BACKEND_DIR = Path(__file__).resolve().parents[3]
ALEMBIC_INI_PATH = BACKEND_DIR / "alembic.ini"


def get_alembic_config(db_url: str | None = None) -> Config:
    """Construct Alembic Config object pointed to backend/alembic.ini."""
    if not ALEMBIC_INI_PATH.exists():
        raise FileNotFoundError(f"Alembic configuration file not found at {ALEMBIC_INI_PATH}")

    config = Config(str(ALEMBIC_INI_PATH))

    migrations_dir = str(BACKEND_DIR / "app" / "infrastructure" / "database" / "migrations")
    versions_dir = str(
        BACKEND_DIR / "app" / "infrastructure" / "database" / "migrations" / "versions"
    )
    config.set_main_option("path_separator", "os")
    config.set_main_option("script_location", migrations_dir)
    config.set_main_option("version_locations", versions_dir)

    if db_url is None:
        settings = get_settings()
        db_url = (
            f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
            f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
        )

    config.set_main_option("sqlalchemy.url", db_url)
    return config


def upgrade_head(db_url: str | None = None) -> None:
    """Programmatically run 'alembic upgrade head'."""
    logger.info("Executing Alembic database schema upgrade to head...")
    alembic_cfg = get_alembic_config(db_url)
    command.upgrade(alembic_cfg, "head")
    logger.info("Alembic schema upgrade complete.")


def downgrade_revision(revision: str = "-1", db_url: str | None = None) -> None:
    """Programmatically run 'alembic downgrade <revision>'."""
    logger.info("Executing Alembic database schema downgrade to %s...", revision)
    alembic_cfg = get_alembic_config(db_url)
    command.downgrade(alembic_cfg, revision)
    logger.info("Alembic schema downgrade complete.")


def get_current_head_revision(db_url: str | None = None) -> list[str]:
    """Get the current head revision identifiers."""
    alembic_cfg = get_alembic_config(db_url)
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_cfg)
    return list(script.get_heads())
