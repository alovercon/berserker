"""
berserker.session.instruction — AGENTS.md instruction file discovery and loading.

Provides:
- InstructionLoader class for discovering and loading instruction files
- Upward search algorithm from workdir for AGENTS.md / CLAUDE.md / CONTEXT.md
- Global instruction support via ~/.config/berserker/AGENTS.md
- mtime-based cache for efficient re-loading
- Claim-based dedup to avoid re-injecting same content per message_id
- 10KB size limit with warning

Python 3.8.10 compatible: uses type comments, Optional/Dict/List, no match/case.
"""

from __future__ import annotations

import os
import logging
import time
from typing import Dict, List, Optional, Set, Tuple

from berserker.paths.resolver import get_config_dir

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INSTRUCTION_FILES = [
    "AGENTS.md",
    "CLAUDE.md",
    "CONTEXT.md",  # deprecated but still supported
]

MAX_INSTRUCTION_SIZE = 10 * 1024  # 10KB limit


# ---------------------------------------------------------------------------
# InstructionLoader
# ---------------------------------------------------------------------------


class InstructionLoader(object):
    """Discovers and loads instruction files (AGENTS.md, CLAUDE.md, CONTEXT.md).

    Searches upward from a given directory for instruction files, and also
    checks for global instructions in the config directory.

    Features:
    - mtime-based cache: avoids re-reading unchanged files
    - Claim-based dedup: tracks which files have been injected per message_id
    - Size limit: warns and truncates if content exceeds 10KB

    Usage:
        loader = InstructionLoader()
        instructions = loader.load_instructions()
        # Returns list of "Instructions from: {path}\\n{content}" strings
    """

    def __init__(self):
        # type: () -> None
        """Initialize the instruction loader with empty cache and claims."""
        # Cache: filepath -> (mtime, content)
        self._cache = {}  # type: Dict[str, Tuple[float, str]]
        # Claims: message_id -> set of filepaths already injected
        self._claims = {}  # type: Dict[str, Set[str]]
        # Session-level injection tracking: session_id -> set of filepaths already injected
        self._session_injected = {}  # type: Dict[str, Set[str]]

    def _find_upward(self, start_dir):
        # type: (str) -> Optional[str]
        """Search upward from start_dir for the first instruction file.

        Walks from start_dir toward the filesystem root, checking for
        AGENTS.md, CLAUDE.md, or CONTEXT.md at each level.

        Args:
            start_dir: Directory to start searching from.

        Returns:
            Path to the first found instruction file, or None.
        """
        current = os.path.abspath(start_dir)
        # Determine the root to stop at (use drive root on Windows, / on Unix)
        prev = None  # type: Optional[str]
        while current != prev:
            for filename in INSTRUCTION_FILES:
                candidate = os.path.join(current, filename)
                if os.path.isfile(candidate):
                    return candidate
            prev = current
            current = os.path.dirname(current)
        return None

    def _load_global_instructions(self):
        # type: () -> Optional[str]
        """Load global instruction file from config directory.

        Checks ~/.config/berserker/AGENTS.md (or platform equivalent).

        Returns:
            Path to global instruction file, or None if not found.
        """
        config_dir = get_config_dir()
        for filename in INSTRUCTION_FILES:
            candidate = os.path.join(config_dir, filename)
            if os.path.isfile(candidate):
                return candidate
        return None

    def _read_file(self, filepath):
        # type: (str) -> Optional[str]
        """Read file content with mtime-based cache.

        If the file's mtime hasn't changed since last read, returns cached content.

        Args:
            filepath: Path to the file to read.

        Returns:
            File content, or None if file cannot be read.
        """
        try:
            mtime = os.path.getmtime(filepath)
        except OSError:
            return None

        # Check cache
        cached = self._cache.get(filepath)
        if cached is not None:
            cached_mtime, cached_content = cached
            if cached_mtime == mtime:
                return cached_content

        # Read file
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
        except (IOError, OSError, UnicodeDecodeError) as exc:
            logger.debug("Failed to read instruction file %s: %s", filepath, exc)
            return None

        # Enforce size limit
        if len(content) > MAX_INSTRUCTION_SIZE:
            logger.warning(
                "Instruction file %s exceeds 10KB limit (%d bytes), truncating",
                filepath,
                len(content),
            )
            content = (
                content[:MAX_INSTRUCTION_SIZE] + "\n\n[Instruction truncated: exceeded 10KB limit]"
            )

        # Update cache
        self._cache[filepath] = (mtime, content)
        return content

    def _format_instruction(self, filepath, content):
        # type: (str, str) -> str
        """Format instruction content with source path prefix.

        Args:
            filepath: Source file path.
            content: File content.

        Returns:
            Formatted instruction string.
        """
        return "Instructions from: {filepath}\n{content}".format(
            filepath=filepath,
            content=content,
        )

    def load_instructions(self, workdir=None, session_id=None):
        # type: (Optional[str], Optional[str]) -> List[str]
        """Discover and load all applicable instruction files.

        Searches for:
        1. Project-level: upward from workdir (or cwd) for AGENTS.md/CLAUDE.md/CONTEXT.md
        2. Global-level: ~/.config/berserker/AGENTS.md

        Args:
            workdir: Working directory to search from. Defaults to os.getcwd().
            session_id: Optional session ID (reserved for future use, currently ignored).

        Returns:
            List of formatted instruction strings. Always returns current content.
        """
        if workdir is None:
            workdir = os.getcwd()

        results = []  # type: List[str]

        # 1. Project-level instruction file
        project_path = self._find_upward(workdir)
        if project_path is not None:
            content = self._read_file(project_path)
            if content:
                formatted = self._format_instruction(project_path, content)
                results.append(formatted)
                logger.debug("Loaded project instruction: %s", project_path)

        # 2. Global-level instruction file
        global_path = self._load_global_instructions()
        if global_path is not None:
            # Avoid duplicate if global path matches project path
            if global_path != project_path:
                content = self._read_file(global_path)
                if content:
                    formatted = self._format_instruction(global_path, content)
                    results.append(formatted)
                    logger.debug("Loaded global instruction: %s", global_path)

        return results

    def is_claimed(self, message_id, filepath):
        # type: (str, str) -> bool
        """Check if a filepath has already been claimed (injected) for a message.

        Args:
            message_id: The message identifier.
            filepath: The instruction file path to check.

        Returns:
            True if the filepath has already been claimed for this message.
        """
        claimed = self._claims.get(message_id)
        if claimed is None:
            return False
        return filepath in claimed

    def claim(self, message_id, filepath):
        # type: (str, str) -> None
        """Mark a filepath as claimed (injected) for a message.

        Args:
            message_id: The message identifier.
            filepath: The instruction file path to claim.
        """
        if message_id not in self._claims:
            self._claims[message_id] = set()
        self._claims[message_id].add(filepath)

    def clear_claims(self, message_id):
        # type: (str) -> None
        """Clear all claims for a message_id.

        Args:
            message_id: The message identifier to clear claims for.
        """
        self._claims.pop(message_id, None)

    def clear_cache(self):
        # type: () -> None
        """Clear the entire file content cache."""
        self._cache.clear()

    def is_session_injected(self, session_id):
        # type: (str) -> bool
        """Check if instructions have already been injected for a session.

        Args:
            session_id: The session identifier.

        Returns:
            True if instructions have already been injected for this session.
        """
        injected = self._session_injected.get(session_id)
        if injected is None:
            return False
        return True

    def mark_session_injected(self, session_id):
        # type: (str) -> None
        """Mark that instructions have been injected for a session.

        Args:
            session_id: The session identifier.
        """
        self._session_injected[session_id] = True

    def clear_session(self, session_id):
        # type: (str) -> None
        """Clear session injection tracking for a session.

        Args:
            session_id: The session identifier to clear.
        """
        self._session_injected.pop(session_id, None)

    def get_loaded_paths(self):
        # type: () -> Set[str]
        """Get all currently cached instruction file paths.

        Returns:
            Set of filepaths that have been loaded and cached.
        """
        return set(self._cache.keys())

    def scan(self, workdir=None):
        # type: (Optional[str]) -> List[str]
        """Scan for instruction files and return a summary of found files.

        Unlike load_instructions(), this returns just the file paths found
        (not the full content), suitable for display to the user.

        Args:
            workdir: Working directory to search from. Defaults to os.getcwd().

        Returns:
            List of human-readable file path strings for found instruction files.
        """
        if workdir is None:
            workdir = os.getcwd()

        results = []  # type: List[str]

        # 1. Project-level
        project_path = self._find_upward(workdir)
        if project_path is not None:
            results.append(project_path)

        # 2. Global-level
        global_path = self._load_global_instructions()
        if global_path is not None and global_path != project_path:
            results.append(global_path)

        return results


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

instruction_loader = InstructionLoader()
