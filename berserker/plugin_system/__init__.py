"""Plugin system for berserker."""

import importlib
import importlib.util
import json
import logging
import os
import sys
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class Plugin:
    """Base class for berserker plugins.

    Subclasses can override hook methods to integrate with the agent execution lifecycle:

    - on_agent_before_execute(agent_name, messages, session_id):
        Called before an agent starts processing. Return value is ignored.
    - on_agent_after_execute(agent_name, response, session_id):
        Called after an agent completes. Can modify response dict in-place.
    - on_tool_call(tool_name, args, result):
        Called after each tool execution. Return value is ignored.
    """

    def __init__(self, name, version):
        # type: (str, str) -> None
        self.name = name
        self.version = version

    def activate(self):
        # type: () -> None
        """Activate the plugin. Called once during application startup."""
        pass

    def deactivate(self):
        # type: () -> None
        """Deactivate the plugin. Called during shutdown."""
        pass

    # --- Agent execution hooks ---

    def on_agent_before_execute(self, agent_name, messages, session_id):
        # type: (str, List[Any], str) -> None
        """Hook called before an agent starts processing.

        Args:
            agent_name: Name of the agent about to execute.
            messages: List of ChatMessage objects (conversation history).
            session_id: Current session identifier.
        """
        pass

    def on_agent_after_execute(self, agent_name, response, session_id):
        # type: (str, Dict[str, Any], str) -> None
        """Hook called after an agent completes processing.

        Args:
            agent_name: Name of the agent that completed.
            response: Dict with 'content' and 'usage' keys. Can be modified in-place.
            session_id: Current session identifier.
        """
        pass

    def on_tool_call(self, tool_name, args, result):
        # type: (str, Dict[str, Any], Dict[str, Any]) -> None
        """Hook called after each tool execution.

        Args:
            tool_name: Name of the tool that was executed.
            args: Arguments passed to the tool.
            result: Tool execution result dict.
        """
        pass

    # --- Slash command registration (optional) ---

    def get_commands(self):
        # type: () -> List[Dict[str, Any]]
        """Return a list of slash commands this plugin provides.

        This is OPTIONAL — plugins that don't need custom commands
        can skip implementing this method (returns empty list by default).

        Each command dict MUST have:
        - "name": str — Command name with leading slash (e.g., "/timer")
        - "description": str — Short help text (e.g., "Show session timer")
        - "handler": callable — Function(args, session_id) -> Optional[str]
            - args: str (text after command name, may be empty)
            - session_id: current session ID
            - Returns: new session_id if changed, "__exit__" to quit, or None

        Example:
            def get_commands(self):
                return [{
                    "name": "/timer",
                    "description": "Show session timer status",
                    "handler": self._handle_timer,
                }]

            def _handle_timer(self, args, session_id):
                print(self.get_status())
                return None
        """
        return []


class PluginManager:
    """Manages berserker plugins.

    Supports two configuration sources:
    1. Standalone plugins.json (legacy, for CLI install/remove)
    2. Main config.json "plugins" key (preferred, unified config)
    """

    def __init__(self):
        # type: () -> None
        """Initialize plugin manager."""
        self.plugins = {}  # type: Dict[str, Plugin]
        self._activated = False  # type: bool
        self.config_dir = self._get_config_dir()
        self.config_file = os.path.join(self.config_dir, "plugins.json")
        # Auto-load plugins from standalone config file (legacy support)
        self._load_plugins()

    def _get_config_dir(self):
        # type: () -> str
        """Get the configuration directory."""
        if sys.platform == "win32":
            config_dir = os.path.expandvars("%APPDATA%\\berserker")
        else:
            config_dir = os.path.expanduser("~/.config/berserker")
        os.makedirs(config_dir, exist_ok=True)
        return config_dir

    def load_from_config(self, config):
        # type: (Dict[str, Any]) -> None
        """Load plugins from the main config.json 'plugins' key.

        Expected config format:
            {
                "plugins": [
                    {
                        "name": "session-timer",
                        "path": "/path/to/session_timer.py",
                        "version": "1.0.0"
                    }
                ]
            }

        Args:
            config: Full configuration dictionary.
        """
        plugins_config = config.get("plugins", [])
        if not plugins_config:
            return

        for plugin_info in plugins_config:
            name = plugin_info.get("name")
            path = plugin_info.get("path")
            version = plugin_info.get("version", "1.0.0")

            if name and path:
                try:
                    plugin = self._load_plugin_from_path(name, path, version)
                    if plugin and name not in self.plugins:
                        self.plugins[name] = plugin
                except Exception as e:
                    logger.warning("Failed to load plugin '%s' from config: %s", name, e)
                    continue

    def _load_plugins(self):
        # type: () -> None
        """Load plugins from standalone plugins.json (legacy)."""
        if not os.path.exists(self.config_file):
            self.save_config()
            return

        try:
            with open(self.config_file, "r") as f:
                config = json.load(f)

            plugins_config = config.get("plugins", [])
            for plugin_info in plugins_config:
                name = plugin_info.get("name")
                path = plugin_info.get("path")
                version = plugin_info.get("version", "unknown")

                if name and path:
                    try:
                        plugin = self._load_plugin_from_path(name, path, version)
                        if plugin and name not in self.plugins:
                            self.plugins[name] = plugin
                    except Exception as e:
                        logger.warning("Failed to load plugin '%s' from standalone config: %s", name, e)
                        continue
        except (IOError, ValueError) as e:
            logger.warning("Failed to read standalone plugins.json, creating default: %s", e)
            self.save_config()

    def _resolve_plugin_path(self, path):
        # type: (str) -> str
        """Resolve plugin path, supporting relative paths.

        Resolution order:
        1. If absolute path and exists → use as-is
        2. If relative to config_dir and exists → resolve against config_dir
        3. If relative to exe directory and exists → resolve against exe dir
        4. Return original path (will fail existence check)
        """
        if os.path.isabs(path) and os.path.exists(path):
            return path

        # Try relative to config directory
        config_relative = os.path.join(self.config_dir, path)
        if os.path.exists(config_relative):
            return config_relative

        # Try relative to exe directory (for deployed exe + plugins/ layout)
        if getattr(sys, 'frozen', False):
            # Running as PyInstaller exe
            exe_dir = os.path.dirname(sys.executable)
        else:
            # Running as script
            exe_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

        exe_relative = os.path.join(exe_dir, path)
        if os.path.exists(exe_relative):
            return exe_relative

        return path

    def _load_plugin_from_path(self, name, path, version):
        # type: (str, str, str) -> Optional[Plugin]
        """Load a plugin from a file path."""
        resolved_path = self._resolve_plugin_path(path)
        if not os.path.exists(resolved_path):
            return None

        module_name = os.path.splitext(os.path.basename(resolved_path))[0]

        try:
            # Use spec_from_file_location to load directly without sys.path manipulation
            # This is more reliable in frozen (PyInstaller) environments
            spec = importlib.util.spec_from_file_location(module_name, resolved_path)
            if spec is None or spec.loader is None:
                return None

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as e:
            logger.error("Failed to load plugin module '%s' from %s: %s", name, resolved_path, e)
            return None

        # Find a Plugin subclass defined in this module (skip the base Plugin itself)
        plugin_class = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, Plugin)
                and attr is not Plugin
            ):
                plugin_class = attr
                break

        if plugin_class is not None:
            return plugin_class(name, version)

        return None

    def activate_all(self):
        # type: () -> None
        """Activate all loaded plugins. Called once during application startup."""
        if self._activated:
            return
        for name, plugin in self.plugins.items():
            try:
                plugin.activate()
            except Exception as e:
                logger.warning("Plugin '%s' failed to activate: %s", name, e)
                continue
        self._activated = True

    def deactivate_all(self):
        # type: () -> None
        """Deactivate all plugins."""
        for name, plugin in self.plugins.items():
            try:
                plugin.deactivate()
            except Exception as e:
                logger.warning("Plugin '%s' failed to deactivate: %s", name, e)
                continue
        self._activated = False

    # --- Hook dispatch ---

    def dispatch_before_execute(self, agent_name, messages, session_id):
        # type: (str, List[Any], str) -> None
        """Dispatch on_agent_before_execute to all active plugins."""
        if not self._activated:
            return
        for plugin in self.plugins.values():
            try:
                plugin.on_agent_before_execute(agent_name, messages, session_id)
            except Exception as e:
                logger.warning("Plugin '%s' on_agent_before_execute failed: %s", plugin.name, e)
                continue

    def dispatch_after_execute(self, agent_name, response, session_id):
        # type: (str, Dict[str, Any], str) -> None
        """Dispatch on_agent_after_execute to all active plugins."""
        if not self._activated:
            return
        for plugin in self.plugins.values():
            try:
                plugin.on_agent_after_execute(agent_name, response, session_id)
            except Exception as e:
                logger.warning("Plugin '%s' on_agent_after_execute failed: %s", plugin.name, e)
                continue

    def dispatch_tool_call(self, tool_name, args, result):
        # type: (str, Dict[str, Any], Dict[str, Any]) -> None
        """Dispatch on_tool_call to all active plugins."""
        if not self._activated:
            return
        for plugin in self.plugins.values():
            try:
                plugin.on_tool_call(tool_name, args, result)
            except Exception as e:
                logger.warning("Plugin '%s' on_tool_call failed: %s", plugin.name, e)
                continue

    def list_plugins(self):
        # type: () -> List[Dict[str, Any]]
        """Return a list of plugin information dictionaries."""
        plugin_list = []
        for name, plugin in self.plugins.items():
            status = "active" if self._activated else "loaded"
            plugin_list.append({"name": plugin.name, "version": plugin.version, "status": status})
        return plugin_list

    def get_registered_commands(self):
        # type: () -> Dict[str, Dict[str, Any]]
        """Collect all slash commands from loaded plugins.

        Returns:
            Dict mapping command name (lowercase, with slash) to:
            - "handler": callable(args, session_id) -> Optional[str]
            - "description": str — help text
            - "plugin": str — plugin name that registered this command
        """
        commands = {}  # type: Dict[str, Dict[str, Any]]
        for plugin in self.plugins.values():
            try:
                for cmd in plugin.get_commands():
                    name = cmd.get("name", "").lower()
                    if name:
                        commands[name] = {
                            "handler": cmd.get("handler"),
                            "description": cmd.get("description", ""),
                            "plugin": plugin.name,
                        }
            except Exception as e:
                logger.warning("Plugin '%s' get_commands failed: %s", plugin.name, e)
                continue
        return commands

    def install(self, path_or_name):
        # type: (str) -> Optional[Plugin]
        """Install a plugin from a local path."""
        if not os.path.exists(path_or_name):
            return None

        plugin_name = os.path.splitext(os.path.basename(path_or_name))[0]
        plugin_version = "1.0.0"

        plugin = self._load_plugin_from_path(plugin_name, path_or_name, plugin_version)
        if plugin:
            self.plugins[plugin_name] = plugin

            # Update config
            config = {"plugins": []}
            if os.path.exists(self.config_file):
                try:
                    with open(self.config_file, "r") as f:
                        config = json.load(f)
                except (IOError, ValueError):
                    pass

            plugins_list = config.get("plugins", [])
            found = False
            for i, p in enumerate(plugins_list):
                if p.get("name") == plugin_name:
                    plugins_list[i] = {
                        "name": plugin_name,
                        "path": os.path.abspath(path_or_name),
                        "version": plugin_version,
                    }
                    found = True
                    break

            if not found:
                plugins_list.append(
                    {
                        "name": plugin_name,
                        "path": os.path.abspath(path_or_name),
                        "version": plugin_version,
                    }
                )

            config["plugins"] = plugins_list
            self.save_config(config)

            return plugin

        return None

    def remove(self, name):
        # type: (str) -> bool
        """Remove a plugin by name."""
        if name in self.plugins:
            plugin = self.plugins[name]
            try:
                plugin.deactivate()
            except Exception as e:
                logger.warning("Plugin '%s' failed to deactivate during removal: %s", name, e)
            del self.plugins[name]

            # Update config
            config = {"plugins": []}
            if os.path.exists(self.config_file):
                try:
                    with open(self.config_file, "r") as f:
                        config = json.load(f)
                except (IOError, ValueError):
                    pass

            plugins_list = config.get("plugins", [])
            plugins_list = [p for p in plugins_list if p.get("name") != name]
            config["plugins"] = plugins_list
            self.save_config(config)

            return True
        return False

    def save_config(self, config=None):
        # type: (Optional[Dict[str, Any]]) -> None
        """Save plugin configuration to file."""
        if config is None:
            plugins_list = []
            for name, plugin in self.plugins.items():
                path = ""
                if os.path.exists(self.config_file):
                    try:
                        with open(self.config_file, "r") as f:
                            existing_config = json.load(f)
                        for p in existing_config.get("plugins", []):
                            if p.get("name") == name:
                                path = p.get("path", "")
                                break
                    except (IOError, ValueError):
                        pass

                plugins_list.append({"name": plugin.name, "path": path, "version": plugin.version})
            config = {"plugins": plugins_list}

        if not os.path.exists(self.config_dir):
            os.makedirs(self.config_dir)

        with open(self.config_file, "w") as f:
            json.dump(config, f, indent=2)


# Global singleton
plugin_manager = PluginManager()
