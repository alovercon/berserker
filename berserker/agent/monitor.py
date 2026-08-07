"""Agent real-time status monitor.

Tracks the state of all running agents and provides event notifications
for UI updates.
"""

from __future__ import annotations

import time
import threading
import logging
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Agent status constants
STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"


@dataclass
class AgentState:
    """Represents the current state of an agent."""
    
    agent_id: str
    name: str
    status: str = STATUS_IDLE
    task: str = ""                # What the agent is currently doing (human-readable)
    phase: str = ""               # Machine-readable phase: "init" | "thinking" | "tool_call" | "compacting" | "responding" | "done"
    progress: float = 0.0         # Progress within current phase (0.0 - 1.0)
    start_time: float = 0.0
    iteration: int = 0            # Current interaction/tool call round
    token_count: int = 0          # Approximate token usage
    error: str = ""
    
    @property
    def duration(self) -> float:
        """Get the duration the agent has been running."""
        if self.start_time == 0:
            return 0.0
        if self.status in (STATUS_COMPLETED, STATUS_FAILED):
            return getattr(self, '_end_time', time.time()) - self.start_time
        return time.time() - self.start_time


class AgentMonitor:
    """Monitors agent states and provides event notifications."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._agents: Dict[str, AgentState] = {}
        self._listeners: List[Callable[[str, AgentState], None]] = []
    
    def register_agent(self, agent_id: str, name: str, task: str = "") -> None:
        """Register a new agent."""
        with self._lock:
            self._agents[agent_id] = AgentState(
                agent_id=agent_id,
                name=name,
                status=STATUS_RUNNING,
                task=task,
                start_time=time.time()
            )
            agent = self._agents[agent_id]  # capture inside lock
        self._notify_listeners(agent_id, agent)
    
    def update_status(self, agent_id: str, status: str, progress: float = 0.0, error: str = "") -> None:
        """Update an agent's status."""
        agent = None
        with self._lock:
            if agent_id in self._agents:
                agent = self._agents[agent_id]
                agent.status = status
                agent.error = error
                if status in (STATUS_COMPLETED, STATUS_FAILED):
                    agent._end_time = time.time()
        if agent is not None:
            self._notify_listeners(agent_id, agent)
    
    def update_task(self, agent_id: str, task: str) -> None:
        """Update what the agent is currently doing (human-readable label)."""
        agent = None
        with self._lock:
            if agent_id in self._agents:
                agent = self._agents[agent_id]
                agent.task = task
        if agent is not None:
            self._notify_listeners(agent_id, agent)

    def update_phase(self, agent_id: str, phase: str, task: str = "", progress: float = 0.0) -> None:
        """Update agent phase, task label, and progress atomically.

        Args:
            agent_id: Agent identifier.
            phase: Machine-readable phase name ("init" | "thinking" | "tool_call" | "compacting" | "responding" | "done").
            task: Human-readable description of current activity (leave empty to keep current).
            progress: Progress within current phase (0.0 - 1.0).
        """
        agent = None
        with self._lock:
            if agent_id in self._agents:
                agent = self._agents[agent_id]
                agent.phase = phase
                if task:
                    agent.task = task
                agent.progress = progress
        if agent is not None:
            self._notify_listeners(agent_id, agent)

    def update_iteration(self, agent_id: str, iteration: int, token_count: int = 0) -> None:
        """Update agent iteration count and token usage."""
        agent = None
        with self._lock:
            if agent_id in self._agents:
                agent = self._agents[agent_id]
                agent.iteration = iteration
                agent.token_count = token_count
        if agent is not None:
            self._notify_listeners(agent_id, agent)
    
    def get_agent(self, agent_id: str) -> Optional[AgentState]:
        """Get an agent's state."""
        with self._lock:
            return self._agents.get(agent_id)
    
    def get_all_agents(self) -> List[AgentState]:
        """Get all agents."""
        with self._lock:
            return list(self._agents.values())
    
    def add_listener(self, callback: Callable[[str, AgentState], None]) -> None:
        """Add a listener for agent state changes."""
        with self._lock:
            self._listeners.append(callback)
    
    def remove_listener(self, callback: Callable[[str, AgentState], None]) -> None:
        """Remove a listener."""
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)
    
    def _notify_listeners(self, agent_id: str, agent: AgentState) -> None:
        """Notify all listeners of a state change."""
        for callback in self._listeners:
            try:
                callback(agent_id, agent)
            except Exception:
                logger.exception("Error in agent monitor listener")
    
    def clear_completed(self) -> None:
        """Clear completed and failed agents from the monitor."""
        with self._lock:
            to_remove = [
                aid for aid, agent in self._agents.items()
                if agent.status in (STATUS_COMPLETED, STATUS_FAILED)
            ]
            for aid in to_remove:
                self._agents.pop(aid, None)

    def clear_all(self) -> None:
        """Clear ALL agents from the monitor regardless of status."""
        with self._lock:
            self._agents.clear()


# Module-level singleton
agent_monitor = AgentMonitor()
