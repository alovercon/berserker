"""
Logging configuration for berserker.

Provides centralized logging setup with:
- RotatingFileHandler: File logging at DEBUG level (5MB max, 3 backups)
- StreamHandler: Console logging at WARNING level
- Consistent format: [timestamp] [level] [logger] message

Python 3.8.10 compatible: uses type comments, no match/case.

Usage:
    from berserker.logging_config import setup_logging
    setup_logging()  # Call once at application startup
"""

from __future__ import annotations

import atexit
import logging
import os

logger = logging.getLogger(__name__)
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_LOG_FILE = "berserker.log"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MB
_BACKUP_COUNT = 3
_FILE_LOG_LEVEL = logging.DEBUG
_CONSOLE_LOG_LEVEL = logging.WARNING

_LOG_FORMAT = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# Setup Function
# ---------------------------------------------------------------------------


def setup_logging(log_file=None, file_level=None, console_level=None, enabled=None):
    # type: (Optional[str], Optional[int], Optional[int], Optional[bool]) -> None
    """Configure root logger with file and console handlers.

    Should be called once at application startup before any other logging.

    Args:
        log_file: Path to log file (default: berserker.log in cwd).
        file_level: Log level for file handler (default: DEBUG).
        console_level: Log level for console handler (default: WARNING).
        enabled: Whether logging is enabled (default: True). If False,
                 no handlers are added and logging is effectively disabled.
    """
    # Default to enabled if not specified
    if enabled is None:
        enabled = True

    if not enabled:
        # Disable logging - set root logger to CRITICAL+1 to suppress everything
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.CRITICAL + 1)
        root_logger.handlers.clear()
        return

    if log_file is None:
        log_file = _DEFAULT_LOG_FILE

    # Resolve log file path:
    # - Absolute path: use directly, ensure parent directory exists
    # - Relative path: resolve against the standard log directory
    if os.path.isabs(log_file):
        # Ensure parent directory exists for absolute paths
        parent_dir = os.path.dirname(log_file)
        if parent_dir:
            try:
                os.makedirs(parent_dir, exist_ok=True)
            except (IOError, OSError) as e:
                sys.stderr.write("Warning: Cannot create log directory '{}': {}\n".format(parent_dir, e))
                return
    else:
        # Relative path: resolve against standard log directory
        from berserker.paths.resolver import get_log_dir
        log_dir = get_log_dir()
        log_file = os.path.join(log_dir, log_file)
    if file_level is None:
        file_level = _FILE_LOG_LEVEL
    if console_level is None:
        console_level = _CONSOLE_LOG_LEVEL

    # Get root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)  # Capture everything, handlers filter

    # Clear any existing handlers (idempotent)
    root_logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    # File handler - rotating
    try:
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setLevel(file_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except (IOError, OSError) as e:
        # If we can't write to file, log to console only
        sys.stderr.write("Warning: Cannot create log file '{}': {}\n".format(log_file, e))

    # Console handler disabled - file-only logging
    # console_handler = logging.StreamHandler(sys.stderr)
    # console_handler.setLevel(console_level)
    # console_handler.setFormatter(formatter)
    # root_logger.addHandler(console_handler)

    # Suppress verbose DEBUG logs from third-party HTTP client libraries.
    # These libraries log full request/response traces at DEBUG level, which
    # pollutes the log file with noise (e.g., 404 responses from providers
    # that don't support the /v1/models endpoint).
    for _quiet_logger in ("openai", "openai._base_client", "httpx", "httpcore"):
        logging.getLogger(_quiet_logger).setLevel(logging.WARNING)

    # Log startup
    root_logger.info("Logging initialized. Log file: %s", os.path.abspath(log_file))
    root_logger.debug("Python version: %s", sys.version)
    root_logger.debug("Platform: %s", sys.platform)
    root_logger.debug("Working directory: %s", os.getcwd())

    # Register shutdown handler to prevent logging errors during interpreter teardown.
    # During shutdown, __del__ methods may run after builtins are cleared, causing
    # NameError: name 'open' is not defined in RotatingFileHandler.
    def _shutdown_logging():
        # type: () -> None
        try:
            for handler in root_logger.handlers[:]:
                handler.flush()
                handler.close()
            root_logger.handlers.clear()
        except Exception as e:
            logger.warning("Shutdown logging failed: %s", e)
            pass  # Ignore errors during shutdown

    atexit.register(_shutdown_logging)


# ---------------------------------------------------------------------------
# Convenience Functions
# ---------------------------------------------------------------------------


def get_logger(name):
    # type: (str) -> logging.Logger
    """Get a logger with the given name.

    Convenience wrapper around logging.getLogger().

    Args:
        name: Logger name (typically __name__).

    Returns:
        Configured Logger instance.
    """
    return logging.getLogger(name)
