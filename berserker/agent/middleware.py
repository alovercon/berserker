"""
Message middleware system for inter-agent communication.

Provides:
- MessageMiddleware abstract base class
- LoggingMiddleware: logs all messages
- FilteringMiddleware: filters messages by regex pattern
- TransformMiddleware: transforms message content
- MiddlewareChain: chains multiple middleware together

Python 3.8.10 compatible: uses type comments, no 3.9+ features.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from typing import Callable, List, Optional

from berserker.agent.messaging import AgentMessage

logger = logging.getLogger(__name__)


class MessageMiddleware(ABC):
    """Abstract base class for message middleware.

    Subclasses implement process() to inspect, modify, or filter messages.
    """

    @abstractmethod
    def process(self, message):
        # type: (AgentMessage) -> Optional[AgentMessage]
        """Process a message.

        Args:
            message: The AgentMessage to process.

        Returns:
            The (possibly modified) AgentMessage, or None to drop the message.
        """
        pass


class LoggingMiddleware(MessageMiddleware):
    """Middleware that logs all messages passing through.

    Logs at INFO level with message details including from/to agents,
    content preview, and status.
    """

    def __init__(self, logger_name=None):
        # type: (Optional[str]) -> None
        """Initialize logging middleware.

        Args:
            logger_name: Optional custom logger name (default: uses module logger).
        """
        self._logger = logging.getLogger(logger_name or __name__ + ".logging")

    def process(self, message):
        # type: (AgentMessage) -> AgentMessage
        """Log message details and return unchanged.

        Args:
            message: The AgentMessage to log.

        Returns:
            The original message unchanged.
        """
        content_preview = (message.content or "")[:100]
        self._logger.info(
            "Message: %s -> %s | status=%s | content=%s",
            message.from_agent,
            message.to_agent,
            message.status,
            repr(content_preview),
        )
        return message


class FilteringMiddleware(MessageMiddleware):
    """Middleware that filters messages based on a regex pattern.

    Can either drop matching messages entirely or hold them (mark as held).
    """

    ACTION_DROP = "drop"
    ACTION_HOLD = "hold"

    def __init__(self, pattern, action=None):
        # type: (str, Optional[str]) -> None
        """Initialize filtering middleware.

        Args:
            pattern: Regex pattern to match against message content.
            action: Action for matching messages — 'drop' (remove) or 'hold'
                    (mark as held). Default: 'drop'.

        Raises:
            re.error: If pattern is invalid regex.
            ValueError: If action is not 'drop' or 'hold'.
        """
        if action is None:
            action = self.ACTION_DROP
        if action not in (self.ACTION_DROP, self.ACTION_HOLD):
            raise ValueError(
                "Action must be '{}' or '{}', got '{}'".format(
                    self.ACTION_DROP, self.ACTION_HOLD, action
                )
            )
        self._pattern = re.compile(pattern)
        self._action = action

    def process(self, message):
        # type: (AgentMessage) -> Optional[AgentMessage]
        """Filter message based on pattern match.

        Args:
            message: The AgentMessage to filter.

        Returns:
            The message if it doesn't match (or action is 'hold'),
            None if it matches and action is 'drop'.
        """
        if self._pattern.search(message.content or ""):
            if self._action == self.ACTION_DROP:
                logger.debug(
                    "Dropping message %s (pattern matched)", message.message_id[:8]
                )
                return None
            elif self._action == self.ACTION_HOLD:
                logger.debug(
                    "Holding message %s (pattern matched)", message.message_id[:8]
                )
                message.metadata["_held"] = True
                return message
        return message


class TransformMiddleware(MessageMiddleware):
    """Middleware that transforms message content using a callable.

    Applies the transform function to message.content and updates
    the message in place.
    """

    def __init__(self, transform_func):
        # type: (Callable[[str], str]) -> None
        """Initialize transform middleware.

        Args:
            transform_func: Callable that takes a string and returns a string.
        """
        self._transform_func = transform_func

    def process(self, message):
        # type: (AgentMessage) -> AgentMessage
        """Transform message content.

        Args:
            message: The AgentMessage to transform.

        Returns:
            The message with transformed content.
        """
        if message.content:
            message.content = self._transform_func(message.content)
        return message


class MiddlewareChain(object):
    """Chain of middleware executed in registration order.

    Each middleware processes the message and passes the result to the next.
    If any middleware returns None, the chain stops and returns None.

    Usage:
        chain = MiddlewareChain()
        chain.add(LoggingMiddleware())
        chain.add(FilteringMiddleware(r"secret", "drop"))
        result = chain.process(message)
    """

    def __init__(self):
        # type: () -> None
        self._middleware = []  # type: List[MessageMiddleware]

    def add(self, middleware):
        # type: (MessageMiddleware) -> None
        """Add middleware to the end of the chain.

        Args:
            middleware: MessageMiddleware instance to add.
        """
        self._middleware.append(middleware)

    def process(self, message):
        # type: (AgentMessage) -> Optional[AgentMessage]
        """Run message through all middleware in order.

        Args:
            message: The AgentMessage to process.

        Returns:
            The processed message, or None if any middleware dropped it.
        """
        current = message
        for mw in self._middleware:
            current = mw.process(current)
            if current is None:
                return None
        return current

    def __len__(self):
        # type: () -> int
        """Return number of middleware in the chain."""
        return len(self._middleware)
