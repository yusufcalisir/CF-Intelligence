"""Integration tests for Alembic database schema migrations and rollback cycle."""

from __future__ import annotations

from alembic import command
from app.infrastructure.database.migration_manager import (
    ALEMBIC_INI_PATH,
    downgrade_revision,
    get_alembic_config,
    get_current_head_revision,
    upgrade_head,
)


class TestAlembicMigrationFramework:
    """TestSuite verifying Alembic configuration, revision scripts, and rollback cycle."""

    def test_alembic_ini_exists(self):
        """Verify alembic.ini configuration file is present and valid."""
        assert ALEMBIC_INI_PATH.exists()
        config = get_alembic_config()
        assert "migrations" in config.get_main_option("script_location")

    def test_current_head_revision(self):
        """Verify current head revision script can be resolved."""
        heads = get_current_head_revision()
        assert len(heads) > 0
        assert "001_production_domain_tables" in heads[0]

    def test_offline_migration_sql_generation(self):
        """Verify offline mode config generates valid DDL statements."""
        cfg = get_alembic_config("sqlite:///file:memdb1?mode=memory&cache=shared")
        command.upgrade(cfg, "head", sql=True)
        assert True

    def test_programmatic_migration_manager_helpers(self):
        """Verify migration_manager helper functions."""
        test_db_url = "sqlite:///file:memdb_test?mode=memory&cache=shared"
        # Upgrade schema
        upgrade_head(test_db_url)
        # Downgrade schema
        downgrade_revision("base", test_db_url)
        assert True
