"""
berserker.session.title_gen — SessionTitleGenerator for automatic title generation.

Provides automatic session title generation using the built-in "title" agent.
Titles are generated from the first user message and persisted via SessionManager.

Python 3.8.10 compatible: uses type comments, Optional/Union, no match/case.
"""

import logging
import time
import threading
from typing import Any, Dict, List, Optional

from berserker.provider.base import ChatMessage

logger = logging.getLogger(__name__)

# Maximum length for generated titles
MAX_TITLE_LENGTH = 50


class SessionTitleGenerator(object):
    """Generates and persists session titles using the title agent.

    Args:
        session_manager: SessionManager instance for loading sessions and
                         persisting titles.
        agent_manager: AgentManager instance for executing the title agent.
    """

    def __init__(self, session_manager, agent_manager):
        # type: (Any, Any) -> None
        self._session_manager = session_manager
        self._agent_manager = agent_manager

    def generate_title(self, session_id):
        # type: (str) -> str
        """Generate a title for the given session.

        Workflow:
        1. Load session; if it already has a non-empty title, return it.
        2. Get first user message from session history.
        3. If no user messages, return fallback "Conversation {short_id}".
        4. Execute title agent with the first user message.
        5. Truncate response to MAX_TITLE_LENGTH chars.
        6. Persist title via session_manager.update_title().
        7. Return the generated title.

        Args:
            session_id: The session ID to generate a title for.

        Returns:
            The generated (or existing) title string.
        """
        # Step 1: Load session and check for existing title
        session = self._session_manager.load(session_id)
        if session is not None:
            existing_title = session.get("title", "")
            if existing_title:
                return existing_title

        # Step 2: Get first user message
        short_id = session_id[:8]
        messages = self._session_manager.get_messages(session_id)
        user_message = None  # type: Optional[str]
        for msg in messages:
            if msg.get("role") == "user":
                user_message = msg.get("content", "")
                break

        # Step 3: Fallback if no user messages
        if not user_message:
            fallback = "Conversation {}".format(short_id)
            self._save_title(session_id, fallback)
            return fallback

        # Step 4: Execute title agent with retry logic
        max_retries = 2
        title = ""
        for attempt in range(max_retries):
            try:
                chat_messages = [ChatMessage(role="user", content=user_message)]
                result = self._agent_manager.execute(
                    agent_name="title",
                    messages=chat_messages,
                    session_id=session_id,
                    tool_registry=None,
                )
                title = result.get("content", "").strip()
                if title:
                    break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug(
                        "Title agent attempt %d failed for session %s: %s, retrying...",
                        attempt + 1, session_id, str(e)
                    )
                    # Wait 1 second before retry to allow transient network issues to resolve
                    time.sleep(1)
                else:
                    logger.warning(
                        "Title agent failed for session %s after %d attempts: %s",
                        session_id, max_retries, str(e)
                    )

        # Step 5: Fallback if agent returned empty
        if not title:
            title = "Conversation {}".format(short_id)

        # Step 6: Truncate to max length
        if len(title) > MAX_TITLE_LENGTH:
            title = title[:MAX_TITLE_LENGTH]

        # Step 7: Persist and return
        self._save_title(session_id, title)
        return title

    def generate_title_async(self, session_id):
        # type: (str) -> None
        """Generate a title in the background without blocking.

        Spawns a daemon thread that calls generate_title(). All exceptions
        are caught and logged silently. Returns immediately.

        Args:
            session_id: The session ID to generate a title for.
        """

        def _run():
            # type: () -> None
            try:
                self.generate_title(session_id)
            except Exception as e:
                logger.warning(
                    "Async title generation failed for session %s: %s",
                    session_id,
                    str(e),
                )

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _save_title(self, session_id, title):
        # type: (str, str) -> None
        """Persist the title via session_manager.update_title().

        Args:
            session_id: The session ID to update.
            title: The title string to persist.
        """
        try:
            self._session_manager.update_title(session_id, title)
        except AttributeError:
            logger.warning(
                "session_manager.update_title() not available; title not persisted for %s",
                session_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to persist title for session %s: %s", session_id, str(e)
            )


# ---------------------------------------------------------------------------
# Module-level helper for auto-title triggering
# ---------------------------------------------------------------------------

# Global tracker to prevent duplicate title generation attempts per session
_title_triggered = set()  # type: set
_title_triggered_lock = threading.Lock()


def trigger_auto_title(session_id):
    # type: (str) -> None
    """Trigger async title generation for a session if it doesn't have a title.

    This is a module-level helper that can be called from both CLI and GUI
    flows after saving the first user message. It checks if the session
    already has a title and if title generation has already been triggered
    for this session.

    Args:
        session_id: The session ID to potentially generate a title for.
    """
    # Check if we've already triggered title generation for this session
    with _title_triggered_lock:
        if session_id in _title_triggered:
            return
        _title_triggered.add(session_id)

    # Lazy import to avoid circular dependencies
    from berserker.session.manager import session_manager
    from berserker.agent.manager import agent_manager

    # Check if session already has a title
    try:
        session_data = session_manager.load(session_id)
        if session_data is not None and session_data.get("title"):
            return  # Already has a title, no need to generate
    except Exception as e:
        logger.warning("Failed to check existing title for session %s: %s", session_id, str(e))
        return

    # Create generator and trigger async title generation
    generator = SessionTitleGenerator(session_manager, agent_manager)
    generator.generate_title_async(session_id)
