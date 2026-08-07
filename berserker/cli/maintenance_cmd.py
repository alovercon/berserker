"""
berserker.cli.maintenance_cmd — Maintenance CLI commands.

Provides upgrade, uninstall, and database management commands.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from berserker import __version__
from berserker.paths import get_config_dir
from berserker.storage import get_db, run_migrations

logger = logging.getLogger(__name__)


def cmd_upgrade():
    # type: () -> None
    """Print current version and update instructions."""
    print("berserker version {} (beta)".format(__version__))
    print("Check for updates at https://github.com/.../releases")


def cmd_uninstall():
    # type: () -> None
    """Print uninstallation instructions."""
    config_dir = get_config_dir()
    print("To uninstall berserker:")
    print("1. Remove installation directory")
    print("2. Delete config at {}".format(config_dir))
    print("3. Remove from PATH")


def cmd_db_status():
    # type: () -> None
    """Show database status including path, size, and table count."""
    db = get_db()
    db_path = db.path

    # Get file size
    if os.path.exists(db_path):
        file_size = os.path.getsize(db_path)
    else:
        file_size = 0

    # Get table count
    try:
        result = db.fetchone("SELECT COUNT(*) as cnt FROM sqlite_master WHERE type='table'")
        table_count = result["cnt"] if result else 0
    except Exception as e:
        logger.warning("Failed to get table count: %s", e)
        table_count = 0

    print("Database path: {}".format(db_path))
    print("File size: {} bytes".format(file_size))
    print("Table count: {}".format(table_count))


def cmd_db_migrate():
    # type: () -> None
    """Run pending migrations and report status."""
    db = get_db()
    run_migrations(db)
    print("Migrations applied. Database is up to date.")


def cmd_db_reset(force=False):
    # type: (bool) -> None
    """Reset database (delete and reinitialize).

    Args:
        force: If True, actually delete and reset. If False, show warning.
    """
    if not force:
        print("WARNING: This will delete all data. Use --force to confirm.")
        return

    db = get_db()
    db_path = db.path

    # Close database connection if open
    db.close()

    # Delete database file if it exists
    if os.path.exists(db_path):
        os.remove(db_path)

    # Reinitialize database by getting a new instance
    # This will recreate the database with fresh schema
    _ = get_db()

    print("Database reset complete.")
