"""CLI commands for plugin management."""

from __future__ import print_function
import os
import sys
from berserker.plugin_system import PluginManager


def cmd_plugin_list():
    # type: () -> None
    """List all installed plugins."""
    manager = PluginManager()
    plugins = manager.list_plugins()

    if not plugins:
        print("No plugins installed.")
        return

    # Print header
    print("{:<20} | {:<15} | {:<10}".format("NAME", "VERSION", "STATUS"))
    print("-" * 50)

    for plugin in plugins:
        name = plugin["name"]
        version = plugin["version"]
        status = plugin["status"]
        print("{:<20} | {:<15} | {:<10}".format(name, version, status))


def cmd_plugin_install(path):
    # type: (str) -> None
    """Install a plugin from a local path."""
    if not os.path.exists(path):
        print("Error: Path '{}' does not exist.".format(path), file=sys.stderr)
        return

    manager = PluginManager()
    plugin = manager.install(path)

    if plugin is not None:
        print("Installed plugin '{}' v{}".format(plugin.name, plugin.version))
    else:
        print("Failed to install plugin from '{}'".format(path), file=sys.stderr)


def cmd_plugin_remove(name):
    # type: (str) -> None
    """Remove a plugin by name."""
    manager = PluginManager()
    success = manager.remove(name)

    if success:
        print("Removed plugin '{}'".format(name))
    else:
        print("Plugin '{}' not found".format(name), file=sys.stderr)
