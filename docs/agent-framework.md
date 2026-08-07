# Agent Framework Architecture

## Overview

The berserker Agent Framework is a modular, extensible system for managing AI agents. It grew out of a monolithic `AgentManager` class into a layered architecture that separates concerns: schema validation, agent registration, execution orchestration, inter-agent communication, and frontend adaptation.

The framework ships with 10 built-in agents (berserker, plan, executor, general, explore, consultant, critic, compaction, title, summary) and lets you define custom agents through configuration files, programmatic registration, or (with caveats, see below) natural-language generation.

**Execution note.** The schema-driven data model is complete and accurate, but agent *execution* does not go through `BaseAgent.execute()`. `BuiltInAgent.execute()` and `CustomAgent.execute()` are placeholders that return `{"_delegation_pending": True, ...}` (the code comments say "Wire up to AgentManager.execute() in Phase 2"). Real execution happens through `AgentManager.execute()`, which delegates to `AgentExecutor` in `berserker/agent/executor.py`. Keep that in mind as you read on: `BaseAgent` describes an agent; `AgentManager`/`AgentExecutor` run it.

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend Layer                            │
│                                                                 │
│  CLI (berserker.cli)            GUI (berserker.gui)             │
│  conversation.py                GUIController (wxPython)        │
│  drives agent_manager.execute   drives agent_manager.execute    │
│  renders via CLIDisplayAdapter  implements DisplayAdapter       │
│                                                                 │
│  berserker/display/adapter.py   berserker/gui/controller.py     │
│    DisplayAdapter (ABC)         GUIController(DisplayAdapter)   │
│    CLIDisplayAdapter            (no GUIDisplayAdapter class)    │
│                                                                 │
│  Optional abstract bridge: FrontendAdapter                      │
│  berserker/agent/adapter.py (used mainly in tests)              │
└────────────────────────────────┬────────────────────────────────┘
                                 │  AgentManager.execute()
┌────────────────────────────────▼────────────────────────────────┐
│                       Agent Layer                                │
│                                                                 │
│  AgentManager (berserker/agent/manager.py)                      │
│    - legacy _agents (AgentInfo) + _base_agents (BaseAgent)      │
│    - syncs the global AgentRegistry on init                     │
│    - execute() -> AgentExecutor                                 │
│    - compact(), prune_messages(), register(), unregister()      │
│    - load_from_file, load_from_config_dict, discover_and_load   │
│    - send_message, receive_messages                             │
│                                                                 │
│  AgentExecutor (berserker/agent/executor.py)                    │
│    - provider call + tool call loop                             │
│    - permission checks (permission_checker)                     │
│    - auto-compaction, pruning, persistence, abort               │
│    - plugin hooks, agent_monitor updates                        │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│                 Schema / Registry / Factory Layer                │
│                                                                 │
│  AgentSchema (schema.py)        14-field validated dataclass     │
│  validate_agent_schema()        dict -> AgentSchema              │
│  AgentRegistry (registry.py)    thread-safe BaseAgent storage    │
│  registry                       module-level singleton           │
│  create_agent_from_schema /     factory.py                       │
│  create_builtin_agent           _BUILTIN_AGENT_SCHEMAS (10)      │
│  AgentMode / AgentVisibility    modes.py                         │
│                                                                 │
│  config.py / discovery.py       load custom agents from          │
│                                 JSON/JSONC files                │
│  prompt_loader.py + prompts/    external prompt .md files        │
│  generator.py                   LLM generation (see caveat)      │
└────────────────────────────────┬────────────────────────────────┘
                                 │
┌────────────────────────────────▼────────────────────────────────┐
│                        Provider Layer                            │
│                                                                 │
│  ProviderRegistry (berserker/provider/registry.py)              │
│    - provider_registry.get() / get_provider_for_model()         │
│    - provider.chat(messages, model, tools=...)                  │
└─────────────────────────────────────────────────────────────────┘

Support systems (not shown above):
  Permission:  permission_checker, PermissionRuleset, AgentPermissionRule
  Messaging:   message_router (AgentMessage, MessageRouter), middleware chain
  Orchestr.:   Orchestrator (parallel/sequential/fan-out-fan-in)
  Aggregate:   ResultAggregator (CONCAT / MERGE / SUMMARY / CUSTOM)
  Events:      AgentEvent + bus events (berserker.bus)
  Compaction:  CompactionConfig / CompactionStrategy
```

## Core Components

### AgentSchema

`berserker/agent/schema.py`

`AgentSchema` is a validated dataclass that defines the structure of an agent configuration. It has **14 fields**:

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | `str` | Yes | `""` | Unique identifier (lowercase alphanumeric with hyphens) |
| `description` | `str` | Yes | `""` | Human-readable description |
| `mode` | `str` | Yes | `""` | Execution mode: `primary`, `subagent`, `hidden` |
| `model` | `str` | Yes | `""` | Model identifier (e.g., `gpt-4o`) |
| `prompt` | `str` | Yes | `""` | System prompt defining agent behavior |
| `native` | `bool` | No | `False` | `True` for built-in agents |
| `hidden` | `bool` | No | `False` | Hides agent from UI lists |
| `permission` | `str` | No | `"full"` | `full` or `restricted` |
| `options` | `dict` | No | `{}` | Agent-specific options (`tools`, `max_tool_iterations`) |
| `top_p` | `float` | No | `None` | Nucleus sampling (0.0-1.0) |
| `temperature` | `float` | No | `None` | Temperature sampling (0.0-2.0) |
| `color` | `str` | No | `None` | UI display color |
| `variant` | `str` | No | `None` | Model variant identifier |
| `steps` | `int` | No | `None` | Maximum execution steps |

Validation runs automatically in `__post_init__` and covers required fields, mode, permission, sampling ranges, and the name format (`^[a-z][a-z0-9-]*$`). `validate_agent_schema(data)` converts a raw dictionary into a validated `AgentSchema`; `AgentSchema.from_dict()` / `to_dict()` round-trip the fields, silently ignoring unknown keys.

### BaseAgent Hierarchy

`berserker/agent/base.py`

`BaseAgent` is an abstract class that wraps a validated `AgentSchema`. It exposes read-only properties from the schema: `name`, `mode`, `model`, `description`, `permission`, `schema`, `tools` (from `options["tools"]`), `native`, `system_prompt` (the raw prompt), and `max_tool_iterations` (from `options["max_tool_iterations"]`, default 100000).

Methods:

- `can_use_tool(tool_name)` returns `True` for `permission == "full"`; restricted agents must list the tool in `options["tools"]`.
- `get_system_prompt(injections)` applies `str.format(**injections)` to the prompt (missing keys are logged, not raised).
- `execute(messages, session_id, tool_registry, **kwargs)` is abstract and **currently a placeholder** in both concrete subclasses (see the execution note above).

Two concrete subclasses:

- **BuiltInAgent**: sets `native=True` on the schema. Used for the 10 shipped agents.
- **CustomAgent**: sets `native=False`. Used for config-loaded and programmatically registered agents.

Instances compare equal when their schemas are equal, and render a compact `repr`.

### AgentRegistry

`berserker/agent/registry.py`

Thread-safe registry for `BaseAgent` instances. All operations are protected by `threading.RLock`.

```python
from berserker.agent.registry import registry

registry.register(agent)          # Register a BaseAgent (AgentRegistrationError on duplicate)
agent = registry.get("berserker")    # Get by name (AgentNotFoundError if missing)
agents = registry.list_all()      # List all
primary = registry.list_primary() # mode == "primary"
subs = registry.list_subagents()  # mode == "subagent"
by_mode = registry.list_by_mode("hidden")
registry.has("berserker")            # bool
registry.count()                  # int
registry.unregister("my-agent")   # Remove (AgentNotFoundError if missing)
registry.clear()                  # Clear all (testing)
```

The module exports a singleton `registry` for global access. Note there is **no `list_hidden()` method**; use `list_by_mode("hidden")`.

### Factory

`berserker/agent/factory.py`

- `create_agent_from_schema(schema)` returns a `BuiltInAgent` when `schema.native` is `True`, otherwise a `CustomAgent`.
- `create_builtin_agent(name)` looks up `_BUILTIN_AGENT_SCHEMAS` and returns a `BuiltInAgent`; raises `AgentNotFoundError` with the available names otherwise.
- `_BUILTIN_AGENT_SCHEMAS` is the source of truth for the 10 built-in agents.

### AgentManager

`berserker/agent/manager.py`

`AgentManager` is the central orchestrator. A module-level singleton `agent_manager` exists for global use, and both the CLI and GUI call `agent_manager.execute(...)` directly.

Key responsibilities:

- **Dual bookkeeping**: on construction it registers all built-in agents into the legacy `_agents` dict (`AgentInfo`) *and* the global `AgentRegistry` (`BaseAgent`). `get(name)` returns `AgentInfo` (legacy, unchanged), while `get_agent(name)` returns the `BaseAgent` from the registry.
- **Execution**: `execute(agent_name, messages, session_id, tool_registry, on_tool_call=None, on_permission_ask=None, extra=None, abort_event=None)` delegates to `AgentExecutor.execute()`.
- **Compaction & pruning**: `compact(messages, max_tokens=4096)` and `prune_messages(messages, session_id=None)` implement AI-driven summary compaction and tool-output pruning (configurable via `CompactionConfig`).
- **Config loading**: `load_from_file(path)`, `load_from_config_dict(config)`, `load_from_config(config)`, and `discover_and_load()` load custom agents as `CustomAgent` instances while preserving built-ins (duplicates are skipped with a warning).
- **Registration**: `register(agent)` accepts either a `BaseAgent` or a legacy `AgentInfo`; `unregister(name)` removes from both stores.
- **Messaging**: `send_message(to_agent, content, from_agent=..., session_id=..., metadata=...)` and `receive_messages(agent_name)` wrap the message router and publish bus events.
- **Orchestration helpers**: `execute_parallel(tasks)`, `execute_sequential(tasks)`, and `execute_fan_out_fan_in(...)` delegate to the `Orchestrator` singleton.
- **Agent generation**: `generate_agent(description, ...)` and `generate_and_register(description, ...)` exist but currently fail at runtime because `berserker.agent.generator` cannot be imported (see the generator caveat below).

```python
from berserker.agent.manager import AgentManager, agent_manager

manager = AgentManager()  # or use the shared agent_manager singleton
result = manager.execute(
    agent_name="berserker",
    messages=[ChatMessage(role="user", content="Fix the bug")],
    session_id="session-123",
    tool_registry=tool_registry,
    on_tool_call=my_callback,
    on_permission_ask=permission_callback,
    abort_event=abort_event,
)
# result has keys: content, usage, finish_reason, tool_calls_count, tools_used, iterations
```

### AgentExecutor

`berserker/agent/executor.py`

`AgentExecutor(manager)` contains the real execution loop that `AgentManager.execute()` delegates to. For each turn it:

1. Fetches the `AgentInfo` via `manager.get(agent_name)` and registers the run with `agent_monitor`.
2. Dispatches plugin `before_execute` hooks.
3. Resolves the provider from `agent.model` (either `"provider/model"` or a model looked up through `provider_registry.get_provider_for_model`).
4. Builds the system message, injecting AGENTS.md instructions for primary agents and the workspace path, then strips stale system messages from history.
5. Counts tokens and auto-triggers compaction when the configured threshold is crossed.
6. Filters the tool registry to the agent's allowed tool IDs (and `SkillAsTool` instances when `skill` is allowed).
7. Runs the tool-call loop up to `max_tool_iterations`, checking `abort_event`, enforcing permissions, executing tools, truncating oversized tool output, persisting messages, and dispatching `on_tool_call` and plugin `tool_call` hooks.
8. Handles `finish_reason` exhaustively, detects stuck empty-tool loops, and injects a max-steps summary prompt when the iteration cap is reached.
9. Persists the final assistant response and dispatches plugin `after_execute` hooks.

### AgentMode

`berserker/agent/modes.py`

Three execution modes plus helper functions:

| Mode | Description | Built-in Agents |
|------|-------------|-----------------|
| `primary` | Top-level agent interacting directly with the user | berserker, plan, executor |
| `subagent` | Agent delegated by a primary agent for subtasks | general, explore, consultant, critic |
| `hidden` | Internal agent for system operations | compaction, title, summary |

`AgentMode` and `AgentVisibility` are `enum.Enum` classes; `AgentMode.from_string()` is case-insensitive and `valid_values()` lists the modes. Helper functions: `is_primary(mode)`, `is_subagent(mode)`, `is_hidden(mode)`.

## Built-in Agents

`_BUILTIN_AGENT_SCHEMAS` in `factory.py` defines **10 built-in agents**. The old `"build"` agent was renamed to **berserker**; there is no agent named `build` anymore (`registry.get("build")` raises `AgentNotFoundError`).

| Agent | Mode | Permission | Tools | Description |
|-------|------|------------|-------|-------------|
| `berserker` | primary | full | all tool IDs | Primary coding agent for implementation tasks |
| `plan` | primary | restricted | read, ls, glob, grep | Code analysis and planning agent |
| `executor` | primary | full | all except `task` | Master orchestrator that coordinates agents to complete todo lists |
| `general` | subagent | full | all except todo/selection/task | General-purpose subagent for complex multi-step tasks |
| `explore` | subagent | restricted | read, ls, glob, grep | Codebase exploration and discovery subagent |
| `consultant` | subagent | restricted | read, ls, glob, grep, lsp | Pre-planning consultant that surfaces hidden intentions and AI failure points |
| `critic` | subagent | restricted | read, ls, glob, grep | Expert reviewer for work plans |
| `compaction` | hidden | restricted | read, ls, glob, grep | Hidden agent for compressing conversation history |
| `title` | hidden | restricted | read | Hidden agent for generating session titles |
| `summary` | hidden | restricted | read, ls, glob, grep | Hidden agent for generating session summaries |

Tool constants live in `prompts.py` (`_ALL_TOOL_IDS`, `_READ_ONLY_TOOL_IDS`, `_MINIMAL_TOOL_IDS`, `_SUBAGENT_TOOL_IDS`). The legacy `BUILT_IN_AGENTS_CONFIG` list in `manager.py` mirrors these same 10 agents as `AgentInfo` objects for backward compatibility.

## Extension System

### Config-Based Loading

`berserker/agent/config.py`

Custom agents are defined in JSON or JSONC (JSON with comments) files using the `{"agents": [...]}` format:

```json
{
  "agents": [
    {
      "name": "my-agent",
      "description": "My custom agent",
      "mode": "subagent",
      "model": "gpt-4o",
      "prompt": "You are a helpful assistant...",
      "permission": "restricted",
      "options": {
        "tools": ["read", "grep", "glob"]
      }
    }
  ]
}
```

Functions:

- `load_agents_from_config(path)` reads a file, strips JSONC comments, parses, and validates each agent into an `AgentSchema`.
- `load_agents_from_dict(config)` loads from a dict; for built-in agent names that omit `prompt`, it auto-fills the prompt (see Prompt Externalization below).
- `merge_agent_configs(configs)` merges multiple config layers into `Dict[str, AgentSchema]`, later configs overriding earlier ones.
- `strip_jsonc_comments(text)` removes `//` and `/* */` comments while preserving strings.

### Agent Discovery

`berserker/agent/discovery.py`

The framework scans standard paths in priority order (`AGENT_DISCOVERY_PATHS`):

1. **Global**: `{config_dir}/agents/` (platformdirs `user_config_dir("berserker")`, falling back to `~/.config/berserker/agents/` then `~/berserker/agents/`)
2. **Project**: `{cwd}/.berserker/agents/`
3. **Local**: `{cwd}/agents/`

Each directory may contain `.json` or `.jsonc` files in either single-agent form or the `{"agents": [...]}` wrapper form. `discover_agents()` loads and validates all files, deduplicating by name so the higher-priority path wins. `load_agent_file(path)` loads one file.

```python
from berserker.agent.discovery import discover_agents, load_agent_file

schemas = discover_agents()          # Scan all standard paths
schema = load_agent_file("my-agent.json")
```

### LLM-Based Generation (Broken)

`berserker/agent/generator.py`

`AgentGenerator(registry, provider_registry)` was designed to generate agent configurations from natural-language descriptions, parse and validate the LLM's JSON (handling markdown fences), and optionally register the result.

**Caveat:** `generator.py` imports `AGENT_GENERATION_PROMPT` and `AGENT_VALIDATION_PROMPT` from `berserker.agent.prompts`, but those names do **not exist** in `prompts.py`. Importing `berserker.agent.generator` therefore raises `ImportError`, so `AgentGenerator`, `AgentManager.generate_agent()`, and `AgentManager.generate_and_register()` all fail at runtime today. Do not rely on this feature until the import is fixed; use config files or programmatic registration instead.

## Inter-Agent Communication & Orchestration

### Messaging

`berserker/agent/messaging.py`

Thread-safe message passing between agents with per-agent queues:

```python
from berserker.agent.messaging import message_router, AgentMessage

message_router.register_agent("berserker")
message_router.register_agent("plan")

msg = AgentMessage(
    from_agent="berserker",
    to_agent="plan",
    session_id="session-123",
    content="Review this architecture plan",
)
message_router.send(msg)

messages = message_router.receive("plan")   # Returns and clears the queue
pending = message_router.peek("plan")       # Returns without clearing
count = message_router.get_pending_count("plan")
```

`broadcast(message, exclude=None)` fans a copy out to all registered agents except the sender and any exclusions. `clear(agent_name)` empties a queue; `get_registered_agents()` lists them. `AgentMessage` supports `to_dict()` / `from_dict()` and a status field (`pending`, `delivered`, `read`, `failed`). `AgentManager.send_message()` / `receive_messages()` wrap this router and publish `AGENT_MESSAGE_SENT`, `AGENT_MESSAGE_RECEIVED`, and `AGENT_MESSAGE_FAILED` bus events.

### Middleware Chain

`berserker/agent/middleware.py`

Middleware inspects, filters, and transforms messages between agents:

| Middleware | Purpose |
|------------|---------|
| `LoggingMiddleware` | Logs all messages with from/to agents and content preview |
| `FilteringMiddleware` | Drops or holds messages matching a regex pattern |
| `TransformMiddleware` | Applies a callable to transform message content |
| `MiddlewareChain` | Chains multiple middleware in registration order |

```python
from berserker.agent.middleware import MiddlewareChain, LoggingMiddleware, FilteringMiddleware

chain = MiddlewareChain()
chain.add(LoggingMiddleware())
chain.add(FilteringMiddleware(r"secret", "drop"))

result = chain.process(message)  # None if dropped, modified message otherwise
```

### Orchestrator

`berserker/agent/orchestrator.py`

Manages concurrent execution with three modes, plus cancellation and status tracking.

```python
from berserker.agent.orchestrator import Orchestrator, OrchestrationTask, get_orchestrator

task = OrchestrationTask("task-1", "explore", "Find all auth files", "session-1")
tasks = [task, OrchestrationTask("task-2", "explore", "Find all API files", "session-1")]

orchestrator = get_orchestrator(agent_manager, tool_registry)
results = orchestrator.execute_parallel(tasks)     # threads, all start together
results = orchestrator.execute_sequential(tasks)   # stops on failure

result = orchestrator.execute_fan_out_fan_in(
    fan_out_tasks=tasks,
    fan_in_agent="berserker",
    fan_in_prompt="Synthesize the exploration results into a unified report",
    fan_in_session_id="session-1",
)
# Returns: {"fan_out_results": [...], "fan_in_result": {...}}
```

`OrchestrationTask` carries `task_id`, `agent_name`, `prompt`, `session_id`, `timeout`, plus a `status` that moves through `pending` -> `running` -> `completed` / `failed` / `cancelled`, and a `cancel()` method. `Orchestrator.cancel(task_id)`, `get_status(task_id)`, and `get_all_statuses()` manage tracking. `execute_parallel` returns one result dict per task in original order; `execute_fan_out_fan_in` returns `{"fan_out_results": [...], "fan_in_result": {...}}`.

### ResultAggregator

`berserker/agent/result_aggregator.py`

Collects and combines subagent results with four strategies:

| Strategy | Behavior |
|----------|----------|
| `CONCAT` | Concatenates result contents with per-task headers, plus counts |
| `MERGE` | Merges all result dicts (later wins on key conflict), plus `_task_ids` / `_task_count` |
| `SUMMARY` | Statistics summary (counts, total content length, per-task statuses, `all_successful`) |
| `CUSTOM` | Uses a provided callable |

```python
from berserker.agent.result_aggregator import ResultAggregator, AggregationStrategy

aggregator = ResultAggregator()
aggregator.add_result("task-1", {"content": "result 1", "status": "success"})
aggregator.add_result("task-2", {"content": "result 2", "status": "success"})

combined = aggregator.aggregate(AggregationStrategy.CONCAT)
```

Other methods: `get_result(task_id)`, `get_all_results()`, `clear()`, `count()`.

## Permission System

Two systems coexist. The **default** one, used by the executor, is the simple rules-based `permission_checker`.

### PermissionChecker (used at execution time)

`berserker.permission` exports a singleton `permission_checker` and the result constants `ALLOWED = "allowed"`, `DENIED = "denied"`, `NEEDS_ASK = "needs_ask"`.

In `AgentExecutor`, agents with `permission == "full"` skip permission checks entirely; restricted agents are checked via `permission_checker.check(tool_name, args=..., path=...)` before each tool runs. Ask-mode tools invoke the `on_permission_ask(tool_name, args)` callback (denying by default if no callback is supplied), and the whole turn supports an `abort_event`.

### PermissionRuleset (for richer policy)

`berserker/permission/ruleset.py`

A priority-ordered, thread-safe ruleset with glob matching:

```python
from berserker.permission import PermissionRuleset, AgentPermissionRule

ruleset = PermissionRuleset()
ruleset.add_rule(AgentPermissionRule(
    tool="write",
    action="deny",
    path="*.conf",
    agent="berserker",
    priority=10,
))

result = ruleset.check("write", agent="berserker", path="config.conf")  # DENIED
result = ruleset.check("read", agent="berserker", path="config.conf")   # NEEDS_ASK (no matching rule)
```

Rule evaluation order:

1. Sorted by priority descending (higher first).
2. Within the same priority, agent-specific rules before global rules.
3. First matching rule wins.

Actions: `allow` (ALLOWED), `deny` (DENIED), `ask` (NEEDS_ASK). Also `remove_rule(...)`, `get_rules()`, `load_from_config(...)`, and `merge(other)` (which merges via `MergeStrategy.APPEND`).

### Merge Strategies

`berserker/permission/merge.py`

`merge_permissions(base, override, strategy)` with:

| Strategy | Behavior |
|----------|----------|
| `OVERRIDE` | Override rules replace base rules with the same tool+agent |
| `APPEND` | All rules from both; override rules get priority +1000 |
| `INTERSECT` | Only rules existing in both (same tool+agent+path); base action is used |

## Prompt Externalization

### PromptLoader

`berserker/agent/prompt_loader.py`

Loads system prompts from external `.md` files with template-variable substitution:

```python
from berserker.agent.prompt_loader import PromptLoader, load_prompt, discover_prompts

loader = PromptLoader()
prompt = loader.load("plan")            # Loads prompts/plan.md

prompt = loader.load_with_template("plan", {
    "os_environment": "OS: Windows, Python 3.8.10",
})

prompt = load_prompt("plan", variables={"os_environment": "..."})
names = discover_prompts()              # List available prompt names
```

`load(name)` and `load_with_template(name, variables)` raise `FileNotFoundError` when the file is missing; `discover_prompts()` returns the sorted names of all `.md` files in the directory.

### Prompt Files vs Inline Fallback

`berserker/agent/prompts/` contains exactly **7** `.md` files:

```
build.md  compaction.md  explore.md  general.md  plan.md  summary.md  title.md
```

Agents berserker, consultant, critic, and executor have **no** `.md` file, so they use inline fallback prompts. The prompt strings themselves live in `berserker/agent/prompts.py` as `_SYSTEM_PROMPT_BUILD`, `_SYSTEM_PROMPT_PLAN`, `_SYSTEM_PROMPT_GENERAL`, `_SYSTEM_PROMPT_EXPLORE`, `_SYSTEM_PROMPT_COMPACTION`, `_SYSTEM_PROMPT_TITLE`, `_SYSTEM_PROMPT_SUMMARY`, `_SYSTEM_PROMPT_CONSULTANT` (consultant), `_SYSTEM_PROMPT_CRITIC` (critic), and `_SYSTEM_PROMPT_ORCHESTRATOR` (executor). The fallback map `_INLINE_PROMPTS` (agent name -> prompt string) is defined in `manager.py`, with a duplicate copy in `prompts.py`.

The load order is: external file first, inline constant second. `manager._load_system_prompt(name)` tries `PromptLoader().load(name)` and catches `FileNotFoundError` to fall back to `_INLINE_PROMPTS`. `config._get_builtin_prompt(name)` does the same, auto-filling the `prompt` field for built-in names that omit it in a config file. Note that `build.md` still exists in the prompts directory as a legacy filename; the agent that uses that prompt is named **berserker**.

## Frontend Adapters

### DisplayAdapter (rendering output)

`berserker/display/adapter.py`

`DisplayAdapter` is an abstract base class decoupling conversation logic from output. It defines `display_user_message`, `display_assistant_message`, `display_tool_call`, `display_token_usage`, `display_info`, `display_error`, `request_permission`, `request_selection`, `request_input`, `display_instructions`, `display_agent_list`, and `display_skills`.

Two implementations exist:

- **CLIDisplayAdapter** (same module): terminal implementation using `print()`/`input()`, with special formatting for bash output.
- **GUIController** (`berserker/gui/controller.py`): the wxPython GUI controller implements `DisplayAdapter` directly. There is **no `GUIDisplayAdapter` or `WebDisplayAdapter` class** in the codebase.

### FrontendAdapter (agent bridge)

`berserker/agent/adapter.py`

`FrontendAdapter` is an abstract bridge interface that gives frontends `execute_agent(...)`, `cancel_execution()`, `get_agent_status(...)`, `subscribe_events(...)` / `unsubscribe_events(...)`, `_publish_event(...)`, `list_agents()`, and `get_agent(name)`. In practice the shipped CLI (`berserker.cli.conversation`) and GUI (`GUIController`) drive `agent_manager.execute(...)` directly; `FrontendAdapter` is used mainly in tests as a template for custom frontends.

### AgentEvent

`berserker/agent/events.py`

Event types for agent lifecycle:

| Event Constant | Value |
|----------------|-------|
| `AGENT_STARTED` | `agent.started` |
| `AGENT_COMPLETED` | `agent.completed` |
| `AGENT_FAILED` | `agent.failed` |
| `AGENT_TOOL_CALL` | `agent.tool_call` |
| `AGENT_MESSAGE` | `agent.message` |
| `AGENT_STATUS_CHANGED` | `agent.status_changed` |
| `AGENT_CANCELLED` | `agent.cancelled` |

`AgentEvent(event_type, session_id, agent_name, data=None, timestamp=None)` carries these fields and compares equal on event_type/session_id/agent_name/data. Broader system events (compaction, pruning, snapshots, message send/receive/fail) are published through the global `bus` in `berserker.bus`.

## Extension Guide

### Creating a Custom Agent

**Method 1: Config file.** Drop `.json`/`.jsonc` into a discovery directory (e.g. `.berserker/agents/my-agent.json`). The agent is auto-discovered and registered as a `CustomAgent`:

```json
{
  "name": "my-agent",
  "description": "My custom agent",
  "mode": "subagent",
  "model": "gpt-4o",
  "prompt": "You are a specialized agent for...",
  "permission": "restricted",
  "options": {
    "tools": ["read", "grep", "glob"]
  }
}
```

**Method 2: Programmatic registration.**

```python
from berserker.agent.schema import AgentSchema, validate_agent_schema
from berserker.agent.factory import create_agent_from_schema
from berserker.agent.registry import registry

schema = validate_agent_schema({
    "name": "my-agent",
    "description": "My custom agent",
    "mode": "subagent",
    "model": "gpt-4o",
    "prompt": "You are a specialized agent for...",
    "permission": "restricted",
    "options": {"tools": ["read", "grep", "glob"]},
})

agent = create_agent_from_schema(schema)
registry.register(agent)
```

**Method 3: LLM generation.** Not currently usable (the generator module fails to import; see the generator caveat).

### Configuring Permissions

```python
from berserker.permission import PermissionRuleset, AgentPermissionRule
from berserker.permission import merge_permissions, MergeStrategy

ruleset = PermissionRuleset()

ruleset.add_rule(AgentPermissionRule(tool="read", action="allow"))  # all agents
ruleset.add_rule(AgentPermissionRule(
    tool="write", action="deny", path="*.conf", agent="berserker", priority=10
))
ruleset.add_rule(AgentPermissionRule(tool="bash", action="ask"))

ruleset.load_from_config({
    "permissions": [
        {"tool": "read", "action": "allow"},
        {"tool": "write", "action": "deny", "path": "*.conf", "agent": "berserker", "priority": 10},
    ]
})
```

For execution-time enforcement, register rules on the default `permission_checker` (used by `AgentExecutor` for restricted agents), or pass an `on_permission_ask` callback to `AgentManager.execute()`.

### Using External Prompts

Create `berserker/agent/prompts/my-agent.md` with `{variable}` placeholders. `PromptLoader` finds it by name and `BaseAgent.get_system_prompt(injections)` applies the substitution. Agents without an external file fall back to the inline prompt map.

## Migration from the Legacy API

### What Changed

Before, a single monolithic `AgentManager` held `AgentInfo` objects and executed them inline:

```python
from berserker.agent.manager import AgentManager

manager = AgentManager()
agent = manager.get("berserker")   # Returns AgentInfo
result = manager.execute("berserker", messages, session_id, tool_registry)
```

Now the data model is schema-driven (`AgentSchema` -> `BaseAgent`), agents live in a global `AgentRegistry`, and execution is factored into `AgentExecutor`:

```python
from berserker.agent.registry import registry

agent = registry.get("berserker")  # Returns BaseAgent
```

### Backward Compatibility

**The old API still works.** `AgentManager` maintains both a legacy `_agents` dict (`AgentInfo` instances) and `_base_agents` (`BaseAgent` instances), and keeps the global `AgentRegistry` in sync:

- `AgentManager.get(name)` returns `AgentInfo` (unchanged).
- `AgentManager.get_agent(name)` returns the `BaseAgent`.
- `AgentManager.list()`, `list_primary()`, `list_subagents()` return `AgentInfo` objects (unchanged).
- `AgentManager.execute(...)` has the same signature and behavior as before.
- `AgentManager.register(agent)` accepts both `BaseAgent` and `AgentInfo`.

### Field Mapping

| AgentInfo Field | AgentSchema Field | Notes |
|-----------------|-------------------|-------|
| `name` | `name` | Same |
| `mode` | `mode` | Same |
| `model` | `model` | Same |
| `system_prompt` | `prompt` | Renamed |
| `tools` | `options.tools` | Moved into options |
| `description` | `description` | Same |
| `permission` | `permission` | Same |
| `max_tool_iterations` | `options.max_tool_iterations` | Moved into options |

### Migrating Agent Lookup and Listing

| Task | Old API | New API |
|------|---------|---------|
| Get agent | `manager.get(name)` | `registry.get(name)` or `manager.get_agent(name)` |
| List agents | `manager.list()` | `registry.list_all()` |
| List primary | `manager.list_primary()` | `registry.list_primary()` |
| List subagents | `manager.list_subagents()` | `registry.list_subagents()` |
| List by mode | (manual filter) | `registry.list_by_mode(mode)` |
| Register agent | `manager._agents[name] = ...` (or `manager.register(AgentInfo(...))`) | `registry.register(agent)` |
| Create agent | `AgentInfo(...)` | `validate_agent_schema({...})` + `create_agent_from_schema(...)` |
| Execute agent | `manager.execute(...)` | `manager.execute(...)` (unchanged) |

### Config Format Change

There was no standardized config format before; agents were built programmatically. The new format is a JSON/JSONC `{"agents": [...]}` file discovered from the standard paths, or a dict passed to `AgentManager.load_from_config_dict(config)`. Config entries use `AgentSchema` field names (see the Field Mapping table above).

### Permission Migration

Old code checked permissions inline against the agent's `permission` field and `tools` list inside `execute()`. That inline behavior is preserved for backward compatibility, but new policy code should use `PermissionRuleset` / `AgentPermissionRule` (see the Permission System section) or register rules on the default `permission_checker`.

### Deprecation Status

`AgentInfo` (in `berserker/agent/manager.py`) is **already deprecated**: constructing one emits a `DeprecationWarning` pointing to `AgentSchema` and `BaseAgent`. The framework itself still constructs `AgentInfo` objects internally for backward compatibility (with the warning suppressed). New code should use `AgentSchema` + `BaseAgent` + `AgentRegistry`. `AgentInfo` remains importable for the foreseeable future but should not be used in new code.
