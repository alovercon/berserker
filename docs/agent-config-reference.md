# Agent Configuration Reference

## Overview

This document is the reference for configuring agents in berserker. Agents are defined in JSON or JSONC configuration files, validated against the `AgentSchema` dataclass in `berserker/agent/schema.py`, and then either registered into the global `AgentRegistry` (schema path) or merged into the built-in `AgentInfo` table (legacy path). Both paths are described below because they accept different fields and behave differently.

## Configuration File Format

### File Types

| Format | Extension | Comments | Example |
|--------|-----------|----------|---------|
| JSON | `.json` | No | `{"agents": [...]}` |
| JSONC | `.jsonc` | `//` and `/* */` | `{"agents": [...]}` with inline comments |

JSONC comment stripping is string-aware (`strip_jsonc_comments` in `berserker/agent/config.py` and `berserker/config/jsonc.py`), so comment-like text inside string values is preserved.

### Root Structure

A config file for agents has a top-level `agents` key containing a list of agent definitions:

```json
{
  "agents": [
    {
      "name": "my-agent",
      "description": "Description of the agent",
      "mode": "subagent",
      "model": "gpt-4o",
      "prompt": "You are a specialized agent..."
    }
  ]
}
```

The full config file can also carry `providers`, `permissions`, `mcp`, `lsp`, `plugins`, `compaction`, `subagent_timeout`, and `logging` sections. See the demo files in `docs/demo/` for complete examples.

### Variable Substitution

Before a full config is consumed, `berserker.config.loader.load_config` applies substitution to every string value (`berserker/config/substitution.py`):

| Pattern | Replaced with |
|---------|---------------|
| `{env:VAR_NAME}` | `os.environ.get("VAR_NAME", "")` |
| `{file:path}` | Contents of the file at `path`, resolved against the config's base directory |

Substitution runs recursively over nested dicts and lists. Agent prompts, provider API keys, and plugin paths are the common places to use it.

## The Two Loading Layers

There are two distinct ways agent config reaches the running system. They coexist, and understanding the difference is essential.

### Schema path (new)

The schema path is driven by `AgentSchema` and these functions:

- `validate_agent_schema(data)` in `schema.py`: converts a dict into a validated `AgentSchema`.
- `load_agents_from_config(path)` and `load_agents_from_dict(config)` in `config.py`: return `List[AgentSchema]`.
- `discover_agents()` and `load_agent_file(path)` in `discovery.py`: return `List[AgentSchema]` and `AgentSchema`.
- `merge_agent_configs(configs)` in `config.py`: returns `Dict[str, AgentSchema]` (later layers override by name).
- `AgentManager.load_from_file(path)`, `load_from_config_dict(config)`, and `discover_and_load()` in `manager.py`: validate configs, then register non-built-in agents into the registry.

Behavior:

- Each dict is validated against `AgentSchema`. `AgentSchema.from_dict` keeps only the 14 known schema fields and **silently drops everything else**. That means `prompt_append`, top-level `system_prompt`, top-level `tools`, and `max_tool_iterations` are ignored here.
- Built-in agents take precedence. If an agent with a given name is already in the registry (all built-ins are registered at startup), the config definition is skipped. `AgentManager.load_from_file` and `load_from_config_dict` log a warning for this; `discover_and_load` logs at debug level.
- Custom agents that pass validation are created via `create_agent_from_schema` (factory.py) with `native=False`, so they become `CustomAgent` instances.
- If `prompt` is missing for a name in the built-in list (`berserker`, `plan`, `general`, `explore`, `compaction`, `title`, `summary`, `consultant`, `critic`, `executor`), `load_agents_from_dict` auto-fills it from the external prompt file or the inline fallback in `factory.py`. A custom (non-built-in) agent without `prompt` fails validation and is skipped with a warning.

### Legacy config path

The legacy path is `AgentManager.load_from_config(config)` in `manager.py`. It operates on the older `AgentInfo` dataclass (deprecated, raises `DeprecationWarning`), and it accepts a different, looser field set. Per-agent keys it understands:

- `prompt_append` (string): appended to the built-in agent's existing system prompt, separated by two newlines. Only applied when the base prompt is not explicitly overridden.
- `system_prompt` (string): treated as an alias for `prompt` when `prompt` is absent.
- `prompt` (string): full replacement of the system prompt.
- `tools` (list, top-level): replaces the agent's tool list directly.
- `max_tool_iterations` (int): overrides the tool-call loop cap (default 100000).
- `model`, `description`, `permission`, `mode`: standard overrides.
- `options`: consulted for `options.tools` and `options.max_tool_iterations` as a fallback when the top-level keys are absent.

Behavior:

- If the name matches an existing built-in (berserker, plan, general, explore, compaction, title, summary, consultant, critic, executor), the built-in is **updated in place**: model, system prompt, tools, permission, and max_tool_iterations are all replaced with the config values.
- If the name is unknown, the entry is treated as a new custom agent and registered, but it requires a `prompt` (or `system_prompt`) and a `model`; otherwise it is skipped with a warning.
- Hidden agents `title`, `compaction`, and `summary` that are not present in the config inherit the primary agent's model.
- `load_from_config` also loads the `compaction` section into the manager's `CompactionConfig`.

### Which path runs when

The CLI and GUI entry points call `AgentManager.load_from_config` with the merged config dict from `berserker.config.loader.load_config`. The schema-path functions are the public API for loading agent files programmatically, and `load_from_file` / `load_from_config_dict` / `discover_and_load` bridge the two: they use schema validation but register through the registry, where built-ins already win.

**Practical consequence:** a config entry named `build` is stale. `build` is no longer a built-in name (it was renamed to `berserker`), so the schema path treats it as a brand-new custom agent, and because it has no `prompt` it fails validation and is skipped. Rename such entries to `berserker`, or give them a `prompt`.

## Agent Schema Fields

Each agent definition in the schema path is validated against `AgentSchema` (`berserker/agent/schema.py`). The schema has 14 fields: `name`, `description`, `mode`, `model`, `prompt`, `native`, `hidden`, `permission`, `options`, `top_p`, `temperature`, `color`, `variant`, `steps`.

### Required Fields

#### `name` (string)

Unique identifier for the agent. Must match the pattern `^[a-z][a-z0-9-]*$`: it must start with a lowercase letter, then lowercase letters, digits, and hyphens only.

```json
{
  "name": "code-reviewer"
}
```

Validation rules:

- Cannot be empty.
- Must start with a letter.
- Lowercase alphanumeric characters and hyphens only.
- Names like `code-reviewer` and `test-writer` are valid; `0agent`, `My-Agent`, and `my_agent` are not.

#### `description` (string)

Human-readable description of the agent's purpose and capabilities.

```json
{
  "description": "Analyzes code for security vulnerabilities and suggests fixes"
}
```

Cannot be empty.

#### `mode` (string)

Execution mode that determines how the agent is used in the system.

| Value | Description | Example built-ins |
|-------|-------------|-------------------|
| `primary` | Top-level agent that interacts directly with the user | `berserker`, `plan`, `executor` |
| `subagent` | Agent delegated by a primary agent for subtasks | `general`, `explore`, `consultant`, `critic` |
| `hidden` | Internal agent for system operations, hidden from UI | `compaction`, `title`, `summary` |

`AgentMode.from_string` matches case-insensitively, but the canonical values are lowercase: `primary`, `subagent`, `hidden`.

#### `model` (string)

Model identifier for the LLM this agent uses.

```json
{
  "model": "gpt-4o"
}
```

Validation rules:

- Cannot be empty.
- There is **no provider-registry validation at schema time**. `schema.py` only requires a non-empty string. Runtime resolution happens later in the executor:
  - A value with a slash, e.g. `openai/gpt-4o`, is split into `provider_id` and `model_name`, and the provider is looked up directly.
  - A bare value, e.g. `gpt-4o`, is searched across every registered provider's model list via `provider_registry.get_provider_for_model`.

#### `prompt` (string)

System prompt that defines the agent's behavior, personality, and capabilities.

```json
{
  "prompt": "You are a code review specialist. Focus on:\n- Security vulnerabilities\n- Performance issues\n- Code quality"
}
```

Validation rules:

- Cannot be empty.
- Supports multi-line strings with `\n` escape sequences.

**Note:** In the schema path, `prompt` completely replaces the prompt for the agent. There is no schema-level append mechanism. Appending extra instructions to a built-in agent's prompt is only possible through the legacy `AgentManager.load_from_config` path via the `prompt_append` key (see below). A `prompt_append` key on a schema-path agent is silently dropped.

#### `prompt_append` (string, legacy only)

Not an `AgentSchema` field. It exists only in the legacy `AgentManager.load_from_config` path, where it appends additional instructions to the end of a built-in agent's existing system prompt.

```json
{
  "name": "berserker",
  "prompt_append": "\n\nAdditional instructions:\n- Always use Python 3.10+ features\n- Prefer type hints"
}
```

Behavior:

- If `prompt` / `system_prompt` is not specified: appends to the built-in agent's current prompt.
- If `prompt` / `system_prompt` is specified: `prompt_append` is ignored, because the explicit prompt takes precedence.
- The appended text is separated from the base prompt by two newlines (`\n\n`).

The schema path does not read this key at all.

### Optional Fields

#### `native` (boolean)

Indicates whether the agent is built into berserker.

```json
{
  "native": false
}
```

Default: `false`. Built-in schemas in `factory.py` set `native=True`; config-loaded agents are forced to `native=False` when registered, so they become `CustomAgent` instances. Users normally do not set this field.

#### `hidden` (boolean)

Hides the agent from UI agent lists.

```json
{
  "hidden": true
}
```

Default: `false`. This is separate from `mode: "hidden"`. A subagent with `hidden: true` stays usable but is not listed in the UI.

#### `permission` (string)

Controls the agent's tool permission level.

| Value | Description |
|-------|-------------|
| `full` | Agent can use all tools without restriction |
| `restricted` | Agent's tools must pass the permission ruleset; use `options.tools` to scope them |

Default: `"full"`.

#### `options` (object)

Agent-specific options.

```json
{
  "options": {
    "tools": ["read", "grep", "glob", "write"],
    "max_tool_iterations": 50
  }
}
```

Supported keys:

| Key | Type | Description |
|-----|------|-------------|
| `tools` | `string[]` | Allowed tool IDs (used when `permission: "restricted"`) |
| `max_tool_iterations` | `int` | Tool-call loop cap per turn (default 100000) |

`options` is a free-form dict in the schema; `from_dict` copies it as-is. The factory reads `options.tools` and the executor reads `options.max_tool_iterations`.

#### `top_p` (number)

Nucleus sampling parameter.

```json
{
  "top_p": 0.9
}
```

Range: `0.0` to `1.0`. Default: `None` (model default). Must be a number and within range.

#### `temperature` (number)

Temperature sampling parameter.

```json
{
  "temperature": 0.7
}
```

Range: `0.0` to `2.0`. Default: `None` (model default). Must be a number and within range.

#### `color` (string)

UI display color for the agent.

```json
{
  "color": "#3B82F6"
}
```

Default: `None`. Used by frontends to color-code agent messages and indicators.

#### `variant` (string)

Model variant identifier for distinguishing between configurations of the same base model.

```json
{
  "variant": "fast"
}
```

Default: `None`.

#### `steps` (integer)

Maximum number of execution steps for the agent.

```json
{
  "steps": 100
}
```

Default: `None` (no limit).

## Built-in Agents

The 10 built-in agents are defined in `_BUILTIN_AGENT_SCHEMAS` in `berserker/agent/factory.py`. "build" was renamed to **berserker**; no built-in named "build" exists anymore.

| Name | Mode | Permission | Tool scope |
|------|------|------------|------------|
| `berserker` | primary | full | all tools |
| `plan` | primary | restricted | read, ls, glob, grep |
| `general` | subagent | full | all tools except todo, selection, task |
| `explore` | subagent | restricted | read, ls, glob, grep |
| `compaction` | hidden | restricted | read, ls, glob, grep |
| `title` | hidden | restricted | read |
| `summary` | hidden | restricted | read, ls, glob, grep |
| `consultant` | subagent | restricted | read, ls, glob, grep, lsp |
| `critic` | subagent | restricted | read, ls, glob, grep |
| `executor` | primary | full | all tools except task |

The built-in prompt files in `berserker/agent/prompts/` still carry the old names (`build.md`, `plan.md`, `general.md`, `explore.md`, `compaction.md`, `title.md`, `summary.md`). `PromptLoader` looks up `<name>.md`; when the file is missing (for example `berserker.md` does not exist) it falls back to the inline prompt constants in `factory.py`.

## Tool IDs

These are the tool IDs registered by `register_default_tools` (`berserker/tool/init.py`) and its helpers. Use these exact IDs in `options.tools` and in permission rules.

- File reading/writing: `read`, `write`, `ls`, `glob`, `grep`
- Editing: `edit`, `multiedit`, `apply_patch`
- Shell and process: `bash`
- File operations: `mkdir`, `rmdir`, `mv`, `cp`, `rm`, `touch`
- Code intelligence: `lsp` (a single tool covering diagnostics, symbols, go-to-definition, and more)
- Search: `websearch`
- Orchestration: `git`, `todo`, `selection`, `task`, `plan`
- Skills: `skill` (the tool ID is the skill name, registered dynamically)
- MCP: `mcp-exa` plus any tools exposed by MCP servers configured under the `mcp` section

There is no `lsp_diagnostics`, `lsp_symbols`, or `lsp_goto_definition` tool. The LSP capability is one tool whose ID is `lsp`. Configs that reference the split IDs will not behave as intended; replace them with `lsp`.

## Discovery Paths

The framework discovers agent config files from standard paths in priority order (`AGENT_DISCOVERY_PATHS` in `berserker/agent/discovery.py`):

| Priority | Path | Scope | Description |
|----------|------|-------|-------------|
| 1 (Highest) | `{config_dir}/agents/` | Global | Config directory from `_get_config_dir()`, described below |
| 2 | `{cwd}/.berserker/agents/` | Project | Project-scoped agents, committed to version control |
| 3 (Lowest) | `{cwd}/agents/` | Local | Current-directory agents, typically for temporary use |

### Path Resolution

`_get_config_dir()` in `discovery.py` resolves the global config directory like this:

1. It calls `platformdirs.user_config_dir("berserker")` with **no** `appauthor=False`. On Windows this resolves to `%LOCALAPPDATA%\berserker\berserker` (note the doubled folder); on Linux it is `~/.config/berserker`, and on macOS `~/Library/Application Support/berserker`.
2. If `platformdirs` is unavailable, it falls back to `~/.config/berserker` if that directory exists, then to `~/berserker`.

Do not confuse this with `berserker.paths.get_config_dir`, which is what the main config loader uses for the global `config.json`. That one returns `~/.config/berserker` on every platform (or `$XDG_CONFIG_HOME/berserker` on Unix, or `BERSERKER_CONFIG_DIR` if set). Agent discovery and general configuration therefore use different base directories.

### File Scanning

Each discovery path is scanned for `.json` and `.jsonc` files, sorted alphabetically:

```
{config_dir}/agents/
├── security.json
├── testing.jsonc
└── custom-agents.json

.berserker/agents/
├── project-specific.json
└── code-review.jsonc
```

Each file may contain either a single agent dict (`{"name": ..., ...}`) or the `{"agents": [...]}` wrapper. Invalid files are skipped with a warning.

### Duplicate Resolution

When multiple files define agents with the same name (`discover_agents` in `discovery.py`):

1. Higher-priority paths load first.
2. Within one path, files are processed in alphabetical order.
3. The first occurrence wins; later duplicates are skipped, and the skip is logged with `logger.debug`, not a warning.
4. When discovered agents are registered through `discover_and_load`, duplicates of built-ins are also skipped at debug level.

### Manual Loading

Load a single agent file:

```python
from berserker.agent.discovery import load_agent_file

schema = load_agent_file("/path/to/my-agent.json")  # returns AgentSchema
```

Discover all agents from all discovery paths:

```python
from berserker.agent.discovery import discover_agents

schemas = discover_agents()  # returns List[AgentSchema]
```

## Config Loading API

### `load_agents_from_config(path)`

Read a JSON/JSONC file, strip comments, parse, and validate every agent.

```python
from berserker.agent.config import load_agents_from_config

agents = load_agents_from_config(".berserker/agents/security.json")
# Returns List[AgentSchema]
```

Raises `ValueError` on invalid JSON or a missing `agents` key. Invalid agents are skipped with a warning; built-in names without a `prompt` get it auto-filled.

### `load_agents_from_dict(config)`

Load agents from a dict, expected in `{"agents": [...]}` form.

```python
from berserker.agent.config import load_agents_from_dict

config = {
    "agents": [
        {
            "name": "my-agent",
            "description": "My agent",
            "mode": "subagent",
            "model": "gpt-4o",
            "prompt": "You are..."
        }
    ]
}

agents = load_agents_from_dict(config)  # List[AgentSchema]
```

### `merge_agent_configs(configs)`

Merge multiple config layers. Later layers override earlier ones for agents with the same name.

```python
from berserker.agent.config import merge_agent_configs

base = {"agents": [{"name": "agent-a", "model": "gpt-3.5-turbo", "mode": "subagent", "prompt": "..."}]}
override = {"agents": [{"name": "agent-a", "model": "gpt-4o", "mode": "subagent", "prompt": "..."}]}

merged = merge_agent_configs([base, override])  # Dict[str, AgentSchema]
# merged["agent-a"].model == "gpt-4o"  (override wins)
```

Invalid config layers are skipped with a warning.

### `AgentManager` registration methods

- `load_from_file(path) -> int`: schema-validates a file and registers the agents that are not already registered. Built-ins stay untouched. Returns the number of agents registered.
- `load_from_config_dict(config) -> int`: same for a dict, and also loads the `compaction` section.
- `discover_and_load() -> int`: discovers agents from the standard paths and registers them.
- `load_from_config(config)`: the legacy path described earlier, which updates built-ins in place and honors `prompt_append` / `system_prompt` / top-level `tools` / `max_tool_iterations`.

## Permission Configuration

### Ruleset structure

Permissions are configured separately from agent definitions under a `permissions` list. `PermissionRuleset.load_from_config` in `berserker/permission/ruleset.py` reads it:

```json
{
  "permissions": [
    {
      "tool": "read",
      "action": "allow"
    },
    {
      "tool": "write",
      "action": "deny",
      "path": "*.conf",
      "agent": "berserker",
      "priority": 10
    },
    {
      "tool": "bash",
      "action": "ask"
    }
  ]
}
```

### Rule fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tool` | `string` | Yes | Tool ID to match, supports `fnmatch` glob patterns |
| `action` | `string` | Yes | `allow`, `deny`, or `ask` |
| `path` | `string` | No | File path pattern to match (glob syntax) |
| `agent` | `string` | No | Agent name to scope the rule to; omit for global |
| `priority` | `int` | No | Higher values are evaluated first, default 0 |

### Action types

| Action | Result | Description |
|--------|--------|-------------|
| `allow` | `ALLOWED` | Tool call is permitted without user confirmation |
| `deny` | `DENIED` | Tool call is blocked entirely |
| `ask` | `NEEDS_ASK` | Tool call requires user confirmation |

### Evaluation order

1. Rules are sorted by priority descending (higher first).
2. Within the same priority, agent-specific rules are evaluated before global rules.
3. The first matching rule wins (short-circuit).
4. If nothing matches, the result is `NEEDS_ASK`.

The default permission table (in `berserker/permission/__init__.py`) already allows `read`, `ls`, `glob`, `grep` and asks for `write`, `edit`, `multiedit`, `apply_patch`, `bash`, `task`, `mkdir`, `rmdir`, `mv`, `cp`, `rm`, `touch`. User rules from config are inserted ahead of the defaults, so they take precedence.

### Merge strategies

`merge_permissions(base, override, strategy)` in `berserker/permission/merge.py`:

| Strategy | Behavior |
|----------|----------|
| `OVERRIDE` | Override rules replace base rules with the same `tool` + `agent` combination |
| `APPEND` | All rules from both rulesets are kept; override rules get priority +1000 |
| `INTERSECT` | Only rules present in both rulesets (same `tool` + `agent` + `path`) are kept |

```python
from berserker.permission.merge import merge_permissions, MergeStrategy

merged = merge_permissions(base_ruleset, override_ruleset, MergeStrategy.APPEND)
```

## Prompt Externalization

### Prompt files

System prompts can be externalized to `.md` files in `berserker/agent/prompts/`. The directory currently contains:

```
berserker/agent/prompts/
├── build.md
├── compaction.md
├── explore.md
├── general.md
├── plan.md
├── summary.md
└── title.md
```

### Prompt loading

```python
from berserker.agent.prompt_loader import PromptLoader, load_prompt, discover_prompts

# Load by agent name
prompt = load_prompt("plan")

# Load with template variables
prompt = load_prompt("plan", variables={
    "os_environment": "OS: Windows 10, Python 3.8.10",
    "project_path": "/path/to/project",
})

# Discover available prompts
names = discover_prompts()  # ["build", "compaction", "explore", ...]
```

`PromptLoader.load` looks up `<name>.md`. Template variable substitution uses Python `str.format`, so placeholders are `{variable}`.

### Fallback behavior

If an external prompt file is not found, `PromptLoader` raises `FileNotFoundError`, and the callers (`_get_builtin_prompt` in `config.py`, `_load_system_prompt` in `manager.py`) fall back to the inline prompt constants in `factory.py`.

## Validation Errors

`AgentSchema.__post_init__` validates on construction, and `validate_agent_schema(data)` is the entry point. Errors raise `AgentSchemaValidationError`, whose string form is:

```
Schema validation failed for field '<field>': <message>
```

The per-field messages are:

| Condition | Field | Message |
|-----------|-------|---------|
| Empty name | `name` | `Field 'name' is required and cannot be empty` |
| Invalid name format | `name` | `Name '<name>' must be lowercase alphanumeric with hyphens, starting with a letter` |
| Empty description | `description` | `Field 'description' is required and cannot be empty` |
| Empty mode | `mode` | `Field 'mode' is required and cannot be empty` |
| Invalid mode | `mode` | `Invalid mode '<mode>'. Must be one of: primary, subagent, hidden` |
| Empty model | `model` | `Field 'model' is required and cannot be empty` |
| Empty prompt | `prompt` | `Field 'prompt' is required and cannot be empty` |
| Invalid permission | `permission` | `Invalid permission '<permission>'. Must be one of: full, restricted` |
| top_p not a number | `top_p` | `top_p must be a number, got <type>` |
| top_p out of range | `top_p` | `top_p must be between 0.0 and 1.0, got <value>` |
| temperature not a number | `temperature` | `temperature must be a number, got <type>` |
| temperature out of range | `temperature` | `temperature must be between 0.0 and 2.0, got <value>` |
| Input not a dict | `data` | `Expected a dictionary, got <type>` |

In the loading functions, these errors are caught per agent: the offending agent is skipped and a warning is logged, so one bad agent does not sink the whole file.

## Best Practices

- Use kebab-case names that start with a letter: `code-reviewer`, `test-writer`, `security-auditor`. Avoid `0agent`, underscores, and uppercase.
- Give every custom agent a real `prompt`. Built-ins auto-fill their prompts, custom agents do not.
- Use `permission: "restricted"` plus `options.tools` for agents that need only a few tools. Tool IDs are the exact registered IDs (see Tool IDs above); do not use split LSP IDs.
- For project-specific agents, use `{cwd}/.berserker/agents/`. For personal agents, use the global agent discovery directory.
- Use JSONC for files that need explanatory comments.
- Remember the two layers: schema-driven registration skips names that already exist as built-ins, and only the legacy `load_from_config` path updates built-ins or honors `prompt_append`. If you want to change a built-in's behavior from config, use the legacy path.
- Externalize long prompts to `.md` files under `berserker/agent/prompts/` for easier maintenance.
- Low temperature (`0.1-0.3`) suits deterministic tasks such as code generation and analysis; medium (`0.5-0.7`) suits creative writing; high (`0.8-1.0`) is for highly open-ended output.

## Compaction Configuration

Compaction is configured under a `compaction` key in the config dict. `load_compaction_config(config)` in `berserker/agent/compaction.py` reads `config.get("compaction")`, and `AgentManager.load_from_config` and `load_from_config_dict` pick it up.

```json
{
  "compaction": {
    "auto": true,
    "prune": true,
    "reserved": 20000,
    "prune_protect": 40000,
    "prune_minimum": 20000,
    "compact_threshold": 0.8
  }
}
```

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `auto` | `bool` | `true` | Automatically trigger compaction when the threshold is reached. `false` means manual-only via `/compact` |
| `prune` | `bool` | `true` | Enable pruning of old tool call content to free context space |
| `reserved` | `int` | `20000` | Token buffer reserved for the next response. Compaction triggers when used tokens exceed `model_limit - reserved` |
| `prune_protect` | `int` | `40000` | Protect the last N tokens of tool call content from pruning |
| `prune_minimum` | `int` | `20000` | Minimum total prunable tokens before pruning activates |
| `compact_threshold` | `float` | `0.8` | Fraction of the context window that triggers compaction. The threshold in tokens is `int(model_limit * compact_threshold)` |

`compact_threshold` is a fraction, not an absolute token count. A value like `80000` is meaningful only if the model's context window times 0.8 happens to equal 80000; use a fraction such as `0.8` instead.

### Loading compaction config

```python
from berserker.agent.compaction import load_compaction_config, default_config

# From a full config dict (reads the "compaction" key)
config = load_compaction_config(full_config_dict)

# Defaults
config = default_config()  # CompactionConfig(auto=True, prune=True, reserved=20000, ...)

# Decisions
strategy = CompactionStrategy(config)
if strategy.should_compact(tokens, model_limit):
    ...
threshold = strategy.get_compact_threshold(model_limit)  # int(model_limit * compact_threshold)
```

Unknown keys inside `compaction` are silently ignored by `CompactionConfig.from_dict`.
