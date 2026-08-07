"""
berserker.storage.db — Database initialization, connection, and PRAGMA setup.

Provides:
- Database class: single-connection SQLite wrapper with dict-row access
- get_db(): module-level singleton / context-manager entry point
- PRAGMA setup on every connection (WAL, busy_timeout, cache_size, foreign_keys)
"""

import logging
import os
import sqlite3
import threading
import time
import atexit
from contextlib import contextmanager

from berserker.paths import get_data_dir
from berserker.storage.schema import FULL_SCHEMA

logger = logging.getLogger(__name__)

# Track all created connections for cleanup at exit
_connections = set()  # type: set
_conn_lock = threading.Lock()

@atexit.register
def _close_all_connections():
    for conn in list(_connections):
        try:
            conn.close()
        except Exception:
            pass
    _connections.clear()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_PRAGMAS = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA busy_timeout=5000",
    "PRAGMA cache_size=-64000",
    "PRAGMA foreign_keys=ON",
)

# Backoff delays (seconds) for transient "database is locked" retries.
# WAL + busy_timeout covers most contention; this is the last-resort for
# write bursts so callers never need their own retry loops.
_LOCK_RETRY_DELAYS = (0.2, 0.5, 1.0)


def _db_path():
    """Return the absolute path to the SQLite database file."""
    override = os.environ.get("BERSERKER_DATA_DIR")
    if override:
        db_dir = override
    else:
        db_dir = get_data_dir()

    os.makedirs(db_dir, exist_ok=True)
    return os.path.join(db_dir, "berserker.db")


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


class Database:
    """
    Single-connection SQLite wrapper with sqlite3.Row (dict-like) access.

    Usage:
        db = Database()
        db.execute("CREATE TABLE ...")
        db.commit()
        row = db.fetchone("SELECT * FROM t WHERE id=?", (1,))
        db.close()

    Context manager:
        with Database() as db:
            db.execute(...)
    """

    def __init__(self, path=None):
        self._path = path or _db_path()
        self._conn = None
        self._local = threading.local()

    # -- connection --------------------------------------------------------

    def _get_conn(self):
        """Return (or create) the connection for the current thread."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            return conn

        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        # Track for cleanup at exit
        with _conn_lock:
            _connections.add(conn)

        # Restrict database file permissions to owner-only
        try:
            os.chmod(self._path, 0o600)
        except OSError:
            pass  # Windows may not support Unix permissions

        # Apply PRAGMAs
        for pragma in _PRAGMAS:
            conn.execute(pragma)

        self._local.conn = conn
        return conn

    @property
    def conn(self):
        """Underlying sqlite3.Connection (read-only access)."""
        return self._get_conn()

    @property
    def path(self):
        """Absolute path to the SQLite database file."""
        return self._path

    # -- raw execution -----------------------------------------------------

    def _with_lock_retry(self, op):
        """Run *op*, retrying transient "database is locked" errors with a
        short backoff; any other error is raised immediately."""
        attempt = 0
        while True:
            try:
                return op()
            except sqlite3.OperationalError as exc:
                if "database is locked" not in str(exc) or attempt >= len(
                    _LOCK_RETRY_DELAYS
                ):
                    raise
                time.sleep(_LOCK_RETRY_DELAYS[attempt])
                attempt += 1

    def execute(self, query, params=None):
        """Execute a single SQL statement (retries transient DB locks)."""
        return self._with_lock_retry(
            lambda: self._get_conn().execute(query, params or ())
        )

    def executemany(self, query, params_list):
        """Execute a statement with multiple parameter sets."""
        return self._get_conn().executemany(query, params_list)

    def executescript(self, script):
        """Execute a multi-statement SQL script."""
        return self._get_conn().executescript(script)

    # -- fetching ----------------------------------------------------------

    def fetchone(self, query, params=None):
        """Execute and return a single row as a dict (or None)."""
        cur = self._get_conn().execute(query, params or ())
        row = cur.fetchone()
        return dict(row) if row else None

    def fetchall(self, query, params=None):
        """Execute and return all rows as a list of dicts."""
        cur = self._get_conn().execute(query, params or ())
        return [dict(r) for r in cur.fetchall()]

    # -- transaction control -----------------------------------------------

    def commit(self):
        self._with_lock_retry(lambda: self._get_conn().commit())

    def rollback(self):
        self._get_conn().rollback()

    # -- schema introspection ----------------------------------------------

    def tables(self):
        """Return a list of all user table names."""
        cur = self._get_conn().execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        return [r["name"] for r in cur.fetchall()]

    # -- lifecycle ---------------------------------------------------------

    def close(self):
        """Close the connection for the current thread."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            # Untrack so atexit cleanup doesn't touch a closed connection
            with _conn_lock:
                _connections.discard(conn)
            self._local.conn = None

    def init_schema(self):
        """Create all tables and indexes if they do not exist."""
        self._get_conn().executescript(FULL_SCHEMA)
        self.commit()

    # -- context manager ---------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            self.rollback()
        self.close()
        return False


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_db_instance = None
_db_lock = threading.Lock()


def get_db():
    """
    Return a shared Database instance (thread-safe singleton).

    Each thread gets its own underlying sqlite3 connection.
    """
    global _db_instance
    if _db_instance is None:
        with _db_lock:
            if _db_instance is None:
                _db_instance = Database()
                _db_instance.init_schema()
    return _db_instance


@contextmanager
def db_context(path=None):
    """
    Context manager that yields a fresh Database instance and closes it on exit.

    Usage:
        with db_context() as db:
            db.execute(...)
    """
    db = Database(path=path)
    db.init_schema()
    try:
        yield db
    except Exception as e:
        logger.error("Database context error: %s", e)
        db.rollback()
        raise
    finally:
        db.close()
