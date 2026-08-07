"""Session Timer Plugin — tracks session elapsed time and agent execution metrics."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from berserker.plugin_system import Plugin


# Global registry so the plugin can be accessed after activation
_session_timer = None  # type: Optional[SessionTimerPlugin]


class SessionTimerPlugin(Plugin):
    """Plugin that tracks session elapsed time and agent execution metrics.

    Hooks used:
    - activate(): Start the session timer
    - deactivate(): Stop the session timer
    - on_agent_before_execute(): Record agent start time
    - on_agent_after_execute(): Record agent duration
    - on_tool_call(): Track tool call count

    Usage:
        plugin = SessionTimerPlugin("session-timer", "1.0.0")
        plugin.activate()
        # ... do work ...
        print(plugin.get_elapsed())  # "00:05:32"
        print(plugin.get_agent_stats())  # {"build": {"count": 3, "total_ms": 1234}}
    """

    def __init__(self, name, version):
        # type: (str, str) -> None
        super(SessionTimerPlugin, self).__init__(name, version)
        self._start_time = None  # type: Optional[float]
        self._is_running = False
        self._agent_starts = {}  # type: Dict[str, float]
        self._agent_stats = {}  # type: Dict[str, Dict[str, Any]]
        self._tool_call_count = 0

    def activate(self):
        # type: () -> None
        """Start the session timer."""
        self._start_time = time.time()
        self._is_running = True
        global _session_timer
        _session_timer = self

    def deactivate(self):
        # type: () -> None
        """Stop the session timer and reset state."""
        self._is_running = False
        self._start_time = None
        self._agent_starts.clear()
        self._agent_stats.clear()
        self._tool_call_count = 0
        global _session_timer
        _session_timer = None

    # --- Agent hooks ---

    def on_agent_before_execute(self, agent_name, messages, session_id):
        # type: (str, List[Any], str) -> None
        """Record agent start time."""
        self._agent_starts[agent_name] = time.time()
        if agent_name not in self._agent_stats:
            self._agent_stats[agent_name] = {"count": 0, "total_ms": 0.0}

    def on_agent_after_execute(self, agent_name, response, session_id):
        # type: (str, Dict[str, Any], str) -> None
        """Record agent execution duration."""
        start = self._agent_starts.pop(agent_name, None)
        if start is not None:
            duration_ms = (time.time() - start) * 1000
            if agent_name in self._agent_stats:
                self._agent_stats[agent_name]["count"] += 1
                self._agent_stats[agent_name]["total_ms"] += duration_ms

    def on_tool_call(self, tool_name, args, result):
        # type: (str, Dict[str, Any], Dict[str, Any]) -> None
        """Track tool call count."""
        self._tool_call_count += 1

    # --- Public API ---

    def get_elapsed(self):
        # type: () -> str
        """Return elapsed time as HH:MM:SS string.

        Returns:
            Formatted elapsed time, or "00:00:00" if not running.
        """
        if self._start_time is None:
            return "00:00:00"

        elapsed = time.time() - self._start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = int(elapsed % 60)
        return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)

    def get_elapsed_seconds(self):
        # type: () -> float
        """Return elapsed time in seconds."""
        if self._start_time is None:
            return 0.0
        return time.time() - self._start_time

    def get_start_time(self):
        # type: () -> Optional[str]
        """Return the start time as a formatted string."""
        if self._start_time is None:
            return None
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self._start_time))

    def get_agent_stats(self):
        # type: () -> Dict[str, Dict[str, Any]]
        """Return agent execution statistics.

        Returns:
            Dict mapping agent_name to {"count": int, "total_ms": float}.
        """
        return dict(self._agent_stats)

    def get_tool_call_count(self):
        # type: () -> int
        """Return total tool call count."""
        return self._tool_call_count

    def get_status(self):
        # type: () -> str
        """Return a human-readable status summary."""
        if not self._is_running:
            return "Timer not active"

        parts = [
            "Session started at {}".format(self.get_start_time()),
            "elapsed: {}".format(self.get_elapsed()),
            "Tool calls: {}".format(self._tool_call_count),
        ]

        if self._agent_stats:
            agent_summary = []
            for name, stats in self._agent_stats.items():
                avg_ms = stats["total_ms"] / stats["count"] if stats["count"] > 0 else 0
                agent_summary.append(
                    "{}: {} calls, avg {:.0f}ms".format(name, stats["count"], avg_ms)
                )
            parts.append("Agents: " + ", ".join(agent_summary))

        return " | ".join(parts)

    # --- Slash command registration ---

    def get_commands(self):
        # type: () -> List[Dict[str, Any]]
        """Register the /timer slash command."""
        return [
            {
                "name": "/timer",
                "description": "Show session timer status",
                "handler": self._handle_timer_command,
            }
        ]

    def _handle_timer_command(self, args, session_id):
        # type: (str, str) -> Optional[str]
        """Handle the /timer slash command.

        Args:
            args: Additional arguments (e.g., "reset" to reset timer).
            session_id: Current session ID.

        Returns:
            None (session ID unchanged).
        """
        if args.strip().lower() == "reset":
            self._start_time = time.time()
            self._agent_starts.clear()
            self._agent_stats.clear()
            self._tool_call_count = 0
            print("Timer reset.")
        else:
            print(self.get_status())
        return None


def get_timer():
    # type: () -> Optional[SessionTimerPlugin]
    """Get the active session timer plugin instance."""
    return _session_timer
