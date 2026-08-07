"""Tests for plugin system and session-timer plugin."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

from berserker.plugin_system import Plugin, PluginManager
from berserker.plugin.session_timer import SessionTimerPlugin, get_timer


class TestPluginBase(unittest.TestCase):
    """Test the Plugin base class."""

    def test_plugin_init(self):
        plugin = Plugin("test-plugin", "1.0.0")
        self.assertEqual(plugin.name, "test-plugin")
        self.assertEqual(plugin.version, "1.0.0")

    def test_plugin_activate_deactivate_noop(self):
        plugin = Plugin("test-plugin", "1.0.0")
        # Should not raise
        plugin.activate()
        plugin.deactivate()


class TestSessionTimerPlugin(unittest.TestCase):
    """Test the session-timer plugin."""

    def setUp(self):
        self.plugin = SessionTimerPlugin("session-timer", "1.0.0")

    def tearDown(self):
        self.plugin.deactivate()

    def test_timer_init(self):
        self.assertEqual(self.plugin.name, "session-timer")
        self.assertEqual(self.plugin.version, "1.0.0")
        self.assertFalse(self.plugin._is_running)
        self.assertIsNone(self.plugin._start_time)

    def test_activate_starts_timer(self):
        self.plugin.activate()
        self.assertTrue(self.plugin._is_running)
        self.assertIsNotNone(self.plugin._start_time)

    def test_deactivate_stops_timer(self):
        self.plugin.activate()
        self.plugin.deactivate()
        self.assertFalse(self.plugin._is_running)

    def test_get_elapsed_not_running(self):
        result = self.plugin.get_elapsed()
        self.assertEqual(result, "00:00:00")

    def test_get_elapsed_seconds_not_running(self):
        result = self.plugin.get_elapsed_seconds()
        self.assertEqual(result, 0.0)

    def test_get_elapsed_after_delay(self):
        self.plugin.activate()
        time.sleep(1.1)
        elapsed = self.plugin.get_elapsed_seconds()
        self.assertGreaterEqual(elapsed, 1.0)

    def test_get_elapsed_format(self):
        self.plugin.activate()
        result = self.plugin.get_elapsed()
        # Should match HH:MM:SS format
        parts = result.split(":")
        self.assertEqual(len(parts), 3)
        self.assertTrue(all(p.isdigit() for p in parts))

    def test_get_start_time_format(self):
        self.plugin.activate()
        result = self.plugin.get_start_time()
        self.assertIsNotNone(result)
        # Should match YYYY-MM-DD HH:MM:SS
        self.assertEqual(len(result), 19)
        self.assertEqual(result[4], "-")
        self.assertEqual(result[7], "-")
        self.assertEqual(result[10], " ")

    def test_get_status_not_running(self):
        result = self.plugin.get_status()
        self.assertEqual(result, "Timer not active")

    def test_get_status_running(self):
        self.plugin.activate()
        result = self.plugin.get_status()
        self.assertIn("Session started at", result)
        self.assertIn("elapsed:", result)

    def test_global_registry(self):
        self.assertIsNone(get_timer())
        self.plugin.activate()
        self.assertIs(get_timer(), self.plugin)
        self.plugin.deactivate()
        self.assertIsNone(get_timer())

    def test_agent_hooks_track_stats(self):
        """Test that agent hooks track execution statistics."""
        self.plugin.activate()

        # Simulate agent execution
        self.plugin.on_agent_before_execute("build", [], "test-session")
        time.sleep(0.05)
        self.plugin.on_agent_after_execute("build", {"content": "done"}, "test-session")

        stats = self.plugin.get_agent_stats()
        self.assertIn("build", stats)
        self.assertEqual(stats["build"]["count"], 1)
        self.assertGreater(stats["build"]["total_ms"], 0)

    def test_tool_call_hook_tracks_count(self):
        """Test that tool calls are counted."""
        self.plugin.activate()

        self.plugin.on_tool_call("bash", {"command": "ls"}, {"output": "file.txt"})
        self.plugin.on_tool_call("read", {"file_path": "test.py"}, {"content": "..."})

        self.assertEqual(self.plugin.get_tool_call_count(), 2)

    def test_status_includes_agent_stats(self):
        """Test that status includes agent statistics when available."""
        self.plugin.activate()

        self.plugin.on_agent_before_execute("build", [], "test-session")
        self.plugin.on_agent_after_execute("build", {"content": "done"}, "test-session")
        self.plugin.on_tool_call("bash", {}, {})

        status = self.plugin.get_status()
        self.assertIn("Session started at", status)
        self.assertIn("elapsed:", status)
        self.assertIn("Tool calls: 1", status)
        self.assertIn("Agents:", status)
        self.assertIn("build:", status)


class TestPluginManager(unittest.TestCase):
    """Test the PluginManager."""

    def setUp(self):
        # Create a temporary config directory
        self.temp_dir = tempfile.mkdtemp()
        self.original_config_dir = None

        # Patch PluginManager._get_config_dir to use temp dir
        self.saved_get_config_dir = PluginManager._get_config_dir

        def mock_get_config_dir(self_):
            return self.temp_dir

        PluginManager._get_config_dir = mock_get_config_dir

    def tearDown(self):
        PluginManager._get_config_dir = self.saved_get_config_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_manager_init_creates_config(self):
        manager = PluginManager()
        config_file = os.path.join(self.temp_dir, "plugins.json")
        self.assertTrue(os.path.exists(config_file))

    def test_manager_list_plugins_empty(self):
        manager = PluginManager()
        plugins = manager.list_plugins()
        self.assertEqual(plugins, [])

    def test_manager_install_plugin(self):
        # Create a temporary plugin file
        plugin_file = os.path.join(self.temp_dir, "test_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(
                "from berserker.plugin_system import Plugin\n"
                "class Plugin(Plugin):\n"
                "    pass\n"
            )

        manager = PluginManager()
        result = manager.install(plugin_file)

        self.assertIsNotNone(result)
        self.assertEqual(result.name, "test_plugin")

        plugins = manager.list_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["name"], "test_plugin")

    def test_manager_remove_plugin(self):
        plugin_file = os.path.join(self.temp_dir, "test_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(
                "from berserker.plugin_system import Plugin\n"
                "class Plugin(Plugin):\n"
                "    pass\n"
            )

        manager = PluginManager()
        manager.install(plugin_file)
        result = manager.remove("test_plugin")

        self.assertTrue(result)
        plugins = manager.list_plugins()
        self.assertEqual(len(plugins), 0)

    def test_manager_remove_nonexistent(self):
        manager = PluginManager()
        result = manager.remove("nonexistent")
        self.assertFalse(result)

    def test_manager_persists_config(self):
        plugin_file = os.path.join(self.temp_dir, "test_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(
                "from berserker.plugin_system import Plugin\n"
                "class Plugin(Plugin):\n"
                "    pass\n"
            )

        manager1 = PluginManager()
        manager1.install(plugin_file)

        # Create a new manager instance — should load from config
        manager2 = PluginManager()
        plugins = manager2.list_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["name"], "test_plugin")

    def test_manager_load_from_config(self):
        """Test loading plugins from config.json 'plugins' key."""
        # Create a temporary plugin file
        plugin_file = os.path.join(self.temp_dir, "my_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(
                "from berserker.plugin_system import Plugin\n"
                "class Plugin(Plugin):\n"
                "    pass\n"
            )

        manager = PluginManager()
        config = {
            "plugins": [
                {
                    "name": "my_plugin",
                    "path": plugin_file,
                    "version": "2.0.0",
                }
            ]
        }
        manager.load_from_config(config)

        plugins = manager.list_plugins()
        self.assertEqual(len(plugins), 1)
        self.assertEqual(plugins[0]["name"], "my_plugin")
        self.assertEqual(plugins[0]["version"], "2.0.0")

    def test_manager_load_from_config_empty(self):
        """Test loading from config with no plugins key."""
        manager = PluginManager()
        config = {"providers": [], "agents": []}
        manager.load_from_config(config)
        self.assertEqual(manager.list_plugins(), [])

    def test_manager_activate_all(self):
        """Test that activate_all calls activate on each plugin."""
        plugin_file = os.path.join(self.temp_dir, "active_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(
                "from berserker.plugin_system import Plugin\n"
                "class Plugin(Plugin):\n"
                "    def __init__(self, name, version):\n"
                "        super(Plugin, self).__init__(name, version)\n"
                "        self.activated = False\n"
                "    def activate(self):\n"
                "        self.activated = True\n"
            )

        manager = PluginManager()
        manager.install(plugin_file)
        manager.activate_all()

        plugins = manager.list_plugins()
        self.assertEqual(plugins[0]["status"], "active")

    def test_manager_dispatch_hooks(self):
        """Test that hook dispatch methods work without errors."""
        plugin_file = os.path.join(self.temp_dir, "hook_plugin.py")
        with open(plugin_file, "w") as f:
            f.write(
                "from berserker.plugin_system import Plugin\n"
                "class Plugin(Plugin):\n"
                "    def __init__(self, name, version):\n"
                "        super(Plugin, self).__init__(name, version)\n"
                "        self.before_called = False\n"
                "        self.after_called = False\n"
                "        self.tool_called = False\n"
                "    def on_agent_before_execute(self, agent_name, messages, session_id):\n"
                "        self.before_called = True\n"
                "    def on_agent_after_execute(self, agent_name, response, session_id):\n"
                "        self.after_called = True\n"
                "    def on_tool_call(self, tool_name, args, result):\n"
                "        self.tool_called = True\n"
            )

        manager = PluginManager()
        manager.install(plugin_file)
        manager.activate_all()

        # Dispatch hooks
        manager.dispatch_before_execute("build", [], "test-session")
        manager.dispatch_after_execute("build", {"content": "done"}, "test-session")
        manager.dispatch_tool_call("bash", {"command": "ls"}, {"output": "file.txt"})

        # Verify plugin received hooks
        plugin = manager.plugins.get("hook_plugin")
        self.assertIsNotNone(plugin)
        self.assertTrue(plugin.before_called)
        self.assertTrue(plugin.after_called)
        self.assertTrue(plugin.tool_called)


class TestPluginCommands(unittest.TestCase):
    """Test plugin slash command registration."""

    def test_plugin_get_commands_default_empty(self):
        """Base Plugin.get_commands() returns empty list."""
        plugin = Plugin("no-cmds", "1.0.0")
        self.assertEqual(plugin.get_commands(), [])

    def test_session_timer_registers_timer_command(self):
        """SessionTimerPlugin registers /timer command."""
        timer = SessionTimerPlugin("session-timer", "1.0.0")
        timer.activate()

        commands = timer.get_commands()
        self.assertEqual(len(commands), 1)

        cmd = commands[0]
        self.assertEqual(cmd["name"], "/timer")
        self.assertIn("status", cmd["description"].lower())
        self.assertTrue(callable(cmd["handler"]))

    def test_manager_collects_plugin_commands(self):
        """PluginManager.get_registered_commands() collects commands from all plugins."""
        manager = PluginManager()
        timer = SessionTimerPlugin("session-timer", "1.0.0")
        manager.plugins["session-timer"] = timer
        manager.activate_all()

        commands = manager.get_registered_commands()
        self.assertIn("/timer", commands)
        self.assertEqual(commands["/timer"]["plugin"], "session-timer")
        self.assertTrue(callable(commands["/timer"]["handler"]))

    def test_plugin_command_handler_executes(self):
        """Plugin command handler runs and returns expected output."""
        timer = SessionTimerPlugin("session-timer", "1.0.0")
        timer.activate()

        commands = timer.get_commands()
        handler = commands[0]["handler"]

        # Handler should return None (no session change)
        result = handler("", "test-session")
        self.assertIsNone(result)

    def test_plugin_command_handler_reset(self):
        """Plugin /timer reset command resets timer state."""
        timer = SessionTimerPlugin("session-timer", "1.0.0")
        timer.activate()
        timer._tool_call_count = 5
        timer._agent_stats = {"build": {"count": 2, "total_ms": 100}}

        commands = timer.get_commands()
        handler = commands[0]["handler"]

        result = handler("reset", "test-session")
        self.assertIsNone(result)
        self.assertEqual(timer._tool_call_count, 0)
        self.assertEqual(timer._agent_stats, {})

    def test_manager_commands_empty_when_no_plugins(self):
        """PluginManager returns empty dict when no plugins registered."""
        manager = PluginManager()
        manager.plugins.clear()
        commands = manager.get_registered_commands()
        self.assertEqual(commands, {})


if __name__ == "__main__":
    unittest.main()
