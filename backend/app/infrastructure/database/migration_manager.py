"""Programmatic Alembic Migration Manager.

Provides application startup auto-migration, schema version querying,
and programmatic upgrade/downgrade utilities for single-tenant and multi-tenant setups.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from alembic.config import Config

from alembic import command  # type: ignore[attr-defined]
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
        env_url = os.getenv("ALEMBIC_DATABASE_URL") or os.getenv("DATABASE_URL")
        if env_url:
            db_url = env_url
        elif settings.database_type == "sqlite":
            from app.infrastructure.database import _STORAGE_ROOT

            db_path = os.path.abspath(os.path.join(_STORAGE_ROOT, "cfi_central.db")).replace("\\", "/")
            db_url = f"sqlite:///{db_path}"
        else:
            db_url = (
                f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
                f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
            )

    config.set_main_option("sqlalchemy.url", db_url)
    return config


def _ensure_migrated_or_stamped(alembic_cfg: Config, db_url: str | None) -> None:
    """If database already contains domain tables but no alembic_version table, stamp head.
    Otherwise execute standard upgrade to head.
    """
    url = db_url or alembic_cfg.get_main_option("sqlalchemy.url")
    if not url:
        command.upgrade(alembic_cfg, "head")
        return

    sync_url = url
    if "+aiosqlite" in sync_url:
        sync_url = sync_url.replace("+aiosqlite", "")
    elif "+asyncpg" in sync_url:
        sync_url = sync_url.replace("+asyncpg", "")

    try:
        from sqlalchemy import create_engine, inspect

        engine = create_engine(sync_url)
        insp = inspect(engine)
        table_names = set(insp.get_table_names())
        engine.dispose()

        # If domain tables exist but alembic_version is absent or empty, stamp head to adopt existing schema
        has_version = False
        if "alembic_version" in table_names:
            from sqlalchemy import text

            with engine.connect() as conn:
                res = conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1")).scalar()
                if res:
                    has_version = True

        if not has_version and any(
            t in table_names for t in ("federated_rounds", "consortium_members", "simulation_runs")
        ):
            logger.info("Adopting existing database schema by stamping Alembic head...")
            command.stamp(alembic_cfg, "head")
            return
    except Exception as exc:
        logger.debug("Could not inspect database tables for auto-stamp check: %s", exc)

    command.upgrade(alembic_cfg, "head")


def upgrade_head(db_url: str | None = None) -> None:
    """Programmatically run 'alembic upgrade head' (or stamp if adopting existing tables)."""
    logger.info("Executing Alembic database schema upgrade to head...")
    alembic_cfg = get_alembic_config(db_url)
    _ensure_migrated_or_stamped(alembic_cfg, db_url)
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
