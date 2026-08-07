"""
Thread-safe command registry for berserker.

Manages command discovery, registration, lookup, and scope-based
priority resolution. All mutating operations are protected by a
threading.Lock for GUI compatibility.

Python 3.8.10 compatible.
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Optional

from berserker.command.types import CommandDef

logger = logging.getLogger(__name__)

# Priority order: higher index = higher priority
SCOPE_PRIORITY = ["builtin", "plugin", "user", "project"]


class CommandRegistry(object):
    """Thread-safe registry for command definitions.

    Supports scope-based priority: project > user > builtin > plugin.
    All mutations are protected by threading.Lock.
    """

    def __init__(self):
        # type: () -> None
        self._commands = {}  # type: Dict[str, CommandDef]
        self._lock = threading.Lock()

    def register(self, cmd):
        # type: (CommandDef) -> None
        """Register a single command, respecting scope priority.

        If a command with the same name already exists at a higher
        priority scope, the new command is silently ignored.
        """
        with self._lock:
            self._register_with_priority(cmd)

    def _register_with_priority(self, cmd):
        # type: (CommandDef) -> None
        """Internal: register with priority check (caller must hold lock)."""
        existing = self._commands.get(cmd.name)
        if existing is not None:
            existing_prio = SCOPE_PRIORITY.index(existing.scope) if existing.scope in SCOPE_PRIORITY else -1
            new_prio = SCOPE_PRIORITY.index(cmd.scope) if cmd.scope in SCOPE_PRIORITY else -1
            if new_prio <= existing_prio:
                # New command has lower or equal priority — skip
                return
        self._commands[cmd.name] = cmd

    def get(self, name):
        # type: (str) -> Optional[CommandDef]
        """Look up a command by name."""
        with self._lock:
            return self._commands.get(name)

    def list_all(self):
        # type: () -> Dict[str, CommandDef]
        """Return a copy of all registered commands."""
        with self._lock:
            return dict(self._commands)

    def list_by_scope(self, scope):
        # type: (str) -> List[CommandDef]
        """Return all commands matching the given scope."""
        with self._lock:
            return [c for c in self._commands.values() if c.scope == scope]

    def refresh_project_commands(self, workspace):
        # type: (str) -> None
        """Clear old project commands and scan+register new ones.

        Called when GUI switches workspace. Other scopes
        (user/builtin/plugin) are NOT affected.
        """
        with self._lock:
            # 1. Remove all existing project-scoped commands
            to_remove = [name for name, cmd in self._commands.items()
                         if cmd.scope == "project"]
            for name in to_remove:
                del self._commands[name]

        # 2. Scan new workspace (outside lock to avoid I/O under lock)
        from berserker.command.discovery import discover_project_commands
        new_commands = discover_project_commands(workspace)

        # 3. Register new commands (with priority)
        with self._lock:
            for cmd in new_commands:
                self._register_with_priority(cmd)

    def load_plugin_commands(self):
        # type: () -> None
        """Register plugin commands from the plugin system.

        Plugin commands get scope="plugin" (lowest priority).
        """
        from berserker.plugin_system import plugin_manager

        plugin_commands = plugin_manager.get_registered_commands()
        for cmd_name, cmd_info in plugin_commands.items():
            handler = cmd_info.get("handler")
            cmd_def = CommandDef(
                name=cmd_name.lstrip("/"),
                description=cmd_info.get("description", ""),
                scope="plugin",
            )
            # handler is a class-level attribute (not a dataclass field)
            cmd_def.handler = handler
            self.register(cmd_def)

    def scan(self, workspace):
        # type: (str) -> None
        """Full scan of all command sources.

        Discovers and registers commands from all sources in priority order:
        builtin -> user -> project -> plugin (highest to lowest).
        """
        with self._lock:
            self._commands.clear()

        # 1. Builtin commands (lowest priority among sources above plugin)
        from berserker.command.builtin import register_builtin_commands
        register_builtin_commands(self)

        # 2. User + project commands (user < project, resolved by discover_all_commands)
        from berserker.command.discovery import discover_all_commands
        all_cmds = discover_all_commands(workspace)
        with self._lock:
            for cmd in all_cmds.values():
                self._register_with_priority(cmd)

        # 3. Plugin commands (lowest priority — overridden by everything else)
        self.load_plugin_commands()

    def get_help_text(self):
        # type: () -> str
        """Generate help text from all registered commands, grouped by scope."""
        with self._lock:
            if not self._commands:
                return "No commands registered."

            lines = ["Available commands:"]

            # Group by scope
            by_scope = {}  # type: Dict[str, List[CommandDef]]
            for cmd in self._commands.values():
                by_scope.setdefault(cmd.scope, []).append(cmd)

            # Scope display labels
            scope_labels = {
                "builtin": "Built-in commands",
                "user": "User commands",
                "project": "Project commands",
                "plugin": "Plugin commands",
            }

            for scope in ["builtin", "user", "project", "plugin"]:
                cmds = by_scope.get(scope)
                if cmds:
                    lines.append("")
                    lines.append("  {}:".format(scope_labels.get(scope, scope)))
                    for cmd in sorted(cmds, key=lambda c: c.name):
                        hint = " {}".format(cmd.argument_hint) if cmd.argument_hint else ""
                        lines.append("    /{}{} — {}".format(cmd.name, hint, cmd.description))

            return "\n".join(lines)


# Global singleton
command_registry = CommandRegistry()
