# Plugin System Guide

## Overview

The berserker plugin system lets you extend the application without touching its
core. Plugins can:

- Run code when the app starts up and shuts down (`activate` / `deactivate`).
- Hook into the agent execution lifecycle (`on_agent_before_execute`,
  `on_agent_after_execute`, `on_tool_call`).
- Register custom slash commands that work in both the CLI and the GUI.

Plugins are plain Python files loaded from disk at runtime. They are never
compiled into the executable, so you can add or update them after deployment
without rebuilding.

The plugin machinery lives in `berserker/plugin_system/`. The one built-in
plugin is the session timer at `berserker/plugin/session_timer.py`.

## How plugins are loaded

There is a single global manager instance:

```python
from berserker.plugin_system import plugin_manager
```

This singleton is created when the `berserker.plugin_system` module is first
imported. Its constructor immediately does two things:

1. Resolves the config directory (see below).
2. Reads the standalone `plugins.json` file in that directory and loads every
   plugin listed there. If the file does not exist, it is created with an
   empty `plugins` list.

Every production entry point then also feeds the main `config.json` to the
manager and activates the loaded plugins:

- **CLI, interactive mode** (`berserker/cli/conversation.py`): calls
  `plugin_manager.load_from_config(config)` and `plugin_manager.activate_all()`
  at startup, then prints `Plugins loaded: <names>` for any active plugins.
- **CLI, one-shot `run` mode** (`berserker/cli/cli.py`): same two calls.
- **GUI** (`berserker/gui/app.py`): same two calls inside `start_gui_app`.

So there are two configuration sources, and both work:

1. **The `plugins` key in the main config file** (documented API). Every entry
   point reads it. This is the recommended way to enable plugins.
2. **A standalone `plugins.json`** in the config directory (legacy). It is
   auto-loaded at import time by the singleton. It is also what the
   `PluginManager.install()` and `PluginManager.remove()` helpers update when
   you add or delete a plugin programmatically.

### Activation

`activate_all()` calls `activate()` on every loaded plugin exactly once and
sets an internal `_activated` flag. All hook dispatch methods check that flag
first, so hooks only fire after activation. `deactivate_all()` calls
`deactivate()` on each plugin and clears the flag.

## Config directory and file locations

The config directory is resolved from the environment:

| Platform | Config directory |
|---|---|
| Windows | `%APPDATA%\berserker` (for example `C:\Users\<you>\AppData\Roaming\berserker`) |
| macOS / Linux | `~/.config/berserker` |

The directory is created if it does not exist. The legacy plugin list lives at
`<config_dir>/plugins.json`.

Note: this is plain `%APPDATA%` / `$HOME/.config` resolution in the code, not
the `platformdirs` library.

## Deployment layouts

### Development (running from source)

```
berserker/                 # project checkout
├── berserker/
│   ├── plugin/
│   │   └── session_timer.py
│   └── plugin_system/
│       └── __init__.py
└── config.json
```

### EXE deployment (PyInstaller)

```
berserker-gui.exe          # or berserker.exe
config.json
plugins/
├── session_timer.py
└── my_custom_plugin.py
```

Plugins are loaded with `importlib.util.spec_from_file_location`, which reads
the module straight from its path. They do not need to be bundled into the
executable, and the loader does not touch `sys.path`, which is what makes this
work in a frozen environment.

## Configuration

### The `plugins` key in config.json

```json
{
  "providers": { ... },
  "agents": [ ... ],
  "plugins": [
    {
      "name": "session-timer",
      "path": "./plugins/session_timer.py",
      "version": "1.0.0"
    }
  ]
}
```

Each entry has three fields:

| Field | Type | Notes |
|---|---|---|
| `name` | string | Unique plugin name. Used as the key in the manager and in `/help`. |
| `path` | string | Path to the plugin file. See path resolution below. |
| `version` | string | Optional. Defaults to `"1.0.0"` when read from config.json. |

### The legacy plugins.json

Same shape, stored at `<config_dir>/plugins.json`:

```json
{
  "plugins": [
    {
      "name": "session-timer",
      "path": "C:\\berserker\\berserker\\plugin\\session_timer.py",
      "version": "1.0.0"
    }
  ]
}
```

When read from `plugins.json` the default version is `"unknown"`. The
`install()` and `remove()` helpers on `PluginManager` read, update, and write
this file, storing the plugin path as an absolute path.

### Path resolution order

`PluginManager` resolves each plugin path by trying, in order:

1. **Absolute path.** If the path is absolute and the file exists, it is used
   as-is.
2. **Relative to the config directory.** `<config_dir>/<path>` is tried.
3. **Relative to the executable directory.** If the app is frozen
   (PyInstaller, `sys.frozen` is truthy) this is the directory containing the
   exe. If running from source it is the `berserker` package directory, which
   is `dirname(dirname(abspath(__file__)))` from inside
   `berserker/plugin_system/__init__.py`. It is **not** the project root.
4. **Fallback.** The original path is returned unchanged, and the load fails
   the later existence check, so the plugin is silently skipped with a warning.

So in a dev checkout, `"path": "plugin/session_timer.py"` resolves to
`<project>/berserker/plugin/session_timer.py`. In an EXE deployment,
`"path": "plugins/session_timer.py"` resolves to `plugins/session_timer.py`
next to the exe.

## The session timer plugin

The built-in example plugin, `berserker/plugin/session_timer.py`, tracks how
long the app has been running and how much agent work has happened.

### Features

- Records when the session started and the elapsed time.
- Tracks each agent's execution count and average duration.
- Counts tool calls.
- Produces a human-readable status summary.
- Registers the `/timer` slash command, available in both the CLI and GUI.

### Enabling it

Add it to your config `plugins` list. In a source checkout either use an
absolute path or one relative to the config directory:

```json
{
  "plugins": [
    {
      "name": "session-timer",
      "path": "<project-path>/berserker/plugin/session_timer.py",
      "version": "1.0.0"
    }
  ]
}
```

Start the app (GUI or CLI) and the plugin is loaded and activated
automatically.

### The `/timer` command

| Command | Effect |
|---|---|
| `/timer` | Prints the current session timer status. |
| `/timer reset` | Resets the timer, agent stats, and tool call count. |

The handler treats its argument as raw text: it compares
`args.strip().lower()` against `"reset"`. `"/timer reset"` works, and any other
argument (or none) prints the status.

### Programming interface

```python
from berserker.plugin.session_timer import get_timer

# The active timer instance, or None if the plugin was never activated
timer = get_timer()

if timer:
    # Elapsed time as an HH:MM:SS string ("00:05:32", or "00:00:00" if not running)
    print(timer.get_elapsed())

    # Elapsed time in seconds as a float (0.0 if not running)
    print(timer.get_elapsed_seconds())

    # Session start time as "2026-04-24 10:30:00", or None if not running
    print(timer.get_start_time())

    # Per-agent stats: {"build": {"count": 3, "total_ms": 1234.5}}
    stats = timer.get_agent_stats()

    # Total tool call count
    print(timer.get_tool_call_count())

    # Full human-readable status
    print(timer.get_status())
    # "Session started at 2026-04-24 10:30:00 | elapsed: 00:05:32 | Tool calls: 15 | Agents: build: 3 calls, avg 411ms"
```

Method reference:

| Method | Returns |
|---|---|
| `get_timer()` (module function) | The active `SessionTimerPlugin`, or `None` if the plugin was not activated. |
| `get_elapsed()` | Elapsed time as `"HH:MM:SS"`, or `"00:00:00"` if not running. |
| `get_elapsed_seconds()` | Elapsed time in seconds (`float`), or `0.0` if not running. |
| `get_start_time()` | Start time as `"%Y-%m-%d %H:%M:%S"`, or `None` if not running. |
| `get_agent_stats()` | Dict mapping agent name to `{"count": int, "total_ms": float}`. |
| `get_tool_call_count()` | Total tool calls (`int`). |
| `get_status()` | A human-readable one-line summary, or `"Timer not active"`. |

The timer starts on `activate()` and clears its state on `deactivate()`.
Agent stats are collected by pairing `on_agent_before_execute` (records the
start time) with `on_agent_after_execute` (computes the duration and bumps the
count). Tool calls are counted in `on_tool_call`. Because all three hooks are
dispatched by the current executor (see below), these numbers populate when the
plugin is loaded and activated.

## Writing a custom plugin

### Basic structure

```python
from berserker.plugin_system import Plugin


class MyPlugin(Plugin):
    def __init__(self, name, version):
        super(MyPlugin, self).__init__(name, version)
        # Initialize plugin state here

    def activate(self):
        """Called once at application startup."""
        pass

    def deactivate(self):
        """Called at application shutdown."""
        pass

    # --- Agent lifecycle hooks ---

    def on_agent_before_execute(self, agent_name, messages, session_id):
        """Called before an agent starts processing. Return value is ignored."""
        pass

    def on_agent_after_execute(self, agent_name, response, session_id):
        """Called after an agent completes. Can modify response in-place."""
        # response is a dict with 'content', 'usage', and 'finish_reason' keys
        pass

    def on_tool_call(self, tool_name, args, result):
        """Called after each tool execution. Return value is ignored."""
        pass

    # --- Slash command registration (optional) ---

    def get_commands(self):
        return [{
            "name": "/mycommand",
            "description": "Do something custom",
            "handler": self._handle_mycommand,
        }]

    def _handle_mycommand(self, args, session_id):
        """Handle /mycommand. args is the raw text after the command name."""
        print("Running with args: {}".format(args))
        return None  # None means: keep the current session
```

### Hook reference

| Hook | When it fires | Arguments | Typical use |
|---|---|---|---|
| `activate()` | Once, at startup | none | Allocate resources, start background work. |
| `deactivate()` | At shutdown | none | Clean up, persist state. |
| `on_agent_before_execute()` | Before an agent turn | `agent_name, messages, session_id` | Record a start time, inject context. |
| `on_agent_after_execute()` | After an agent turn | `agent_name, response, session_id` | Measure duration, tweak the response dict. |
| `on_tool_call()` | After each tool call | `tool_name, args, result` | Count tool usage, audit logging. |

All dispatch methods catch exceptions raised inside a hook, log a warning, and
continue. A failing hook never breaks agent execution.

### Hook dispatch in the current executor

The production executor is `AgentExecutor` in
`berserker/agent/executor.py`, reached through `agent_manager.execute()`. It
dispatches all three lifecycle hooks inside `execute()`:

- `on_agent_before_execute` via `dispatch_before_execute` (step 1.6).
- `on_tool_call` via `dispatch_tool_call` after each tool runs.
- `on_agent_after_execute` via `dispatch_after_execute` after the turn
  finishes (step 7.5).

Each dispatch is wrapped in its own try/except so a plugin error cannot abort
the agent turn. If a plugin fails at load time (bad path, syntax error, import
error), it is skipped with a logged warning rather than crashing the app.

### Slash command registration

Plugins register commands by implementing `get_commands()`, which returns a
list of command dicts. Each dict must have:

| Field | Type | Notes |
|---|---|---|
| `name` | string | Command name with a leading slash, e.g. `"/timer"`. Stored lowercased. |
| `description` | string | Short help text shown in `/help`. |
| `handler` | callable | `handler(args, session_id) -> Optional[str]`. |

**Handler signature**:

- `args` is a **string**: the raw text after the command name, possibly empty.
  The GUI and CLI both split the input on the first run of whitespace, so
  `/timer reset` passes `"reset"` and `/timer` passes `""`. It is not a list of
  words. Check for a specific argument with something like
  `args.strip().lower() == "reset"`.
- `session_id` is the current session identifier.

**Handler return value**:

- `None`: keep the current session; the handler just does its work.
- A different session id (string): the caller switches to that session. In the
  GUI the controller updates the session and the window title.
- `"__exit__"`: exit the app. The CLI conversation loop honors this and breaks;
  in the GUI use the window close button.

**Discovery**: plugin commands are collected through
`plugin_manager.get_registered_commands()`, which lowercases each command name
and merges them into one dict. They appear in `/help` output alongside the
built-in commands (the command registry gives plugin commands the lowest
priority scope).

### Command output: GUI vs CLI

The two frontends run plugin handlers differently.

- **GUI** (`SlashCommandHandler._execute_plugin_command`): wraps the handler
  call in an `io.StringIO` stdout capture under a lock, then shows whatever the
  handler printed in the chat area. If the handler raises, the error text is
  captured instead. If the handler returns a new session id, the controller and
  the window title are updated. If the handler printed nothing, the chat shows
  `"Command executed successfully."`.
- **CLI** (`_process_slash_command` in `berserker/cli/conversation.py`): calls
  the handler directly as `handler(arg, session_id)` with **no stdout
  capture**. `print()` output goes straight to the console. The returned value
  is used by the conversation loop to switch sessions (`__exit__` quits).

A plugin command's `print()` calls therefore appear in the GUI chat and on the
CLI console, no extra work needed on the plugin side.

### Plugin subclass discovery

When loading a module, the loader scans its attributes and instantiates the
**first** `Plugin` subclass it finds (skipping the `Plugin` base class itself).
The class name does not have to be `Plugin`, but only one subclass per file is
instantiated. Put the class you want loaded first in the file, or keep one
plugin per file.

### Example: a stats command

```python
def get_commands(self):
    return [
        {
            "name": "/stats",
            "description": "Show current session statistics",
            "handler": self._handle_stats,
        },
    ]

def _handle_stats(self, args, session_id):
    # /stats  ->  args == ""
    # /stats verbose  ->  args == "verbose"
    print("Statistics for session {}".format(session_id))
    return None
```

## Registering a plugin

Add the plugin file to the `plugins` list in `config.json`:

```json
{
  "plugins": [
    {
      "name": "my-plugin",
      "path": "/path/to/my_plugin.py",
      "version": "1.0.0"
    }
  ]
}
```

Restart the app. Plugin configuration is read once at startup, so changes to
the file take effect on the next launch.

## Notes and gotchas

1. **One subclass per file, first one wins.** The loader instantiates the first
   `Plugin` subclass found in the module. It does not need to be named
   `Plugin`.
2. **Must subclass** `berserker.plugin_system.Plugin`.
3. **Hook exceptions are isolated.** Errors in `activate`, `deactivate`, or any
   lifecycle hook are caught, logged, and skipped so they cannot interrupt
   agent execution.
4. **Hooks only fire after activation.** The dispatch methods early-return if
   `activate_all()` has not run. All entry points call it at startup.
5. **PyInstaller friendly.** Plugins load via
   `spec_from_file_location` directly from their path, no `sys.path`
   manipulation, so they work frozen.
6. **Global singleton.** Access the shared manager with
   `from berserker.plugin_system import plugin_manager`.
7. **Relative paths are not project-relative.** A relative plugin path resolves
   against the config directory first, then against the exe directory (frozen)
   or the `berserker` package directory (source). Use an absolute path if in
   doubt.
8. **The `plugins.json` file is created on first import.** If it is missing,
   the manager writes an empty one, which is why the directory appears even
   before you configure anything.
