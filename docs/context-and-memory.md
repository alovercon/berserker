# Context and Memory Management

How berserker tracks tokens, manages the LLM context window, persists sessions,
and keeps long conversations within the model's limits. This document is
verified against the current codebase and describes behavior as it actually is,
not how it used to be.

Three mechanisms work together to keep context in check:

1. **Token counting** (`berserker/session/`) decides how many tokens a message
   list costs before the LLM call.
2. **Compaction, pruning, and snapshots** (`berserker/agent/`) shrink the
   history when it approaches the model's context window.
3. **Dynamic truncation** (`berserker/tool/truncate.py`) caps tool output so a
   single oversized result cannot blow the window.

All conversation state lives in a single SQLite file managed by
`SessionManager`, with a per-session runtime object (`SessionContext`) that
coordinates state transitions and aborts.

---

## 1. System architecture overview

```
                     AgentExecutor (agent/executor.py)
  ┌───────────────┐  ┌───────────────┐  ┌──────────────────────────────┐
  │ token counting │  │ compaction    │  │ tool loop + context_info     │
  │ TokenCounter   │  │ _execute_...  │  │ (per tool call)              │
  └───────┬───────┘  └───────┬───────┘  └──────────────┬───────────────┘
          │                  │                          │
          ▼                  ▼                          ▼
  ┌─────────────────┐ ┌──────────────┐ ┌──────────────────────────────┐
  │ QwenTokenizer / │ │ AgentManager │ │ ToolRegistry                  │
  │ tiktoken /      │ │ prune_messages│ │ calculate_dynamic_max_tokens │
  │ estimate_tokens │ │ compact()    │ │ truncate_result()            │
  └─────────────────┘ └──────┬───────┘ └──────────────┬───────────────┘
                             │                        │
                             ▼                        ▼
  ┌────────────────────────────────────────────────────────────────────┐
  │ SessionManager (session/manager.py) — SQLite CRUD + event bus      │
  │ SessionContext (session/context.py) — state machine, abort, cache  │
  │ Storage (storage/) — Database, 15-table schema, migrations         │
  └────────────────────────────────────────────────────────────────────┘
```

**Design principles**

- **Proactive, not reactive.** The executor counts tokens before each LLM call
  and compacts when the count crosses the trigger, rather than waiting for a
  context-length error from the provider.
- **Two-stage shrinking.** Old tool outputs are pruned first (they are bulky
  and least useful), then the remaining history is AI-summarized. If the
  summary still overflows, a fallback truncation drops the oldest messages.
- **Dynamic tool output caps.** Each tool result is truncated to whatever room
  is left in the window, never to a fixed size.
- **Everything persists.** Sessions, messages, pruning state, snapshots, and
  per-session agent config all live in SQLite with WAL mode and cascade deletes.

---

## 2. Token counting chain

### 2.1 `TokenCounter` (`session/token_counter.py`)

`TokenCounter` is a **plain class**, not a singleton. Instances are cheap;
the expensive parts (tiktoken encodings, the Qwen tokenizer) are cached at the
class level, so you can build a new `TokenCounter()` on every call without
paying again.

```python
counter = TokenCounter()
counter.count_text(text, model="gpt-4o")      # single string
counter.count_messages(messages, model="gpt-4o")  # list of ChatMessage, incl. overhead
counter.extract_usage(response)               # {prompt_tokens, completion_tokens, total_tokens}
```

The counting chain inside `count_text`:

```
Qwen model (name contains qwen/qwq/qwen-turbo/qwen-plus/qwen-max)?
  ├─ yes → QwenTokenizer (pure-Python BPE), full vocab preferred
  │        exceptions → language-aware estimate_tokens()
  └─ no  → tiktoken encoding for the model (see map below)
           encoding missing or encode() fails → language-aware estimate_tokens()
```

### 2.2 Model to encoding map

`MODEL_ENCODING_MAP` only knows a handful of model names:

| Model name pattern | Encoding |
|---|---|
| `gpt-4o`, `gpt-4o-mini` | `o200k_base` |
| `gpt-3.5-turbo`, `gpt-3.5-turbo-16k` | `cl100k_base` |
| `gpt-4`, `gpt-4-turbo` | `cl100k_base` |
| `claude-3-opus`, `claude-3-sonnet`, `claude-3-haiku` | `cl100k_base` |
| anything else (including `o1`, `o3`, Gemini, DeepSeek) | `cl100k_base` (the default) |

There is no o1/o3 entry. Unknown models simply fall through to
`cl100k_base`. `get_encoding(model)` caches results (including failed loads)
in the class-level `_encoding_cache` dict and returns `None` when tiktoken is
unavailable.

### 2.3 Per-message overhead (`count_messages`)

`count_messages` adds overhead on top of the raw content:

- `+3` tokens per message (base overhead).
- `+3` extra when `role == "assistant"`.
- The message content, counted with `count_text`.
- `reasoning_content` (DeepSeek-style thinking chains) on assistant messages,
  also counted via `count_text`.
- For each tool call dict that has a `function.name`:
  - `+7` for the tool call name.
  - `+3` if a `function.arguments` key exists, plus the actual token count of
    the arguments payload.

### 2.4 Qwen tokenizer (`session/qwen_tokenizer.py`)

The Qwen tokenizer is a **pure-Python BPE implementation** that does not need
tiktoken's Rust extension, which matters on platforms where that wheel is hard
to install. It mirrors Qwen's official tokenizer:

- `QWEN_PAT_STR`: Qwen's pre-tokenization regex.
- `QWEN_SPECIAL_TOKENS`: Qwen's control tokens (`<|im_start|>`, `<|im_end|>`,
  `<|endoftext|>`, and the vision/FIM/role markers).
- `qwen.tiktoken`: a vocab file of base64-encoded mergeable ranks with integer
  ranks, one pair per line.

`QwenTokenizer`:

```python
tokenizer = QwenTokenizer(vocab_path="/path/to/qwen.tiktoken")
ids = tokenizer.encode("Hello, world!")
tokenizer.count_tokens("Hello, world!")  # len(encode(text))
tokenizer.decode(ids)
```

If no vocab file is given, it loads an **embedded minimal vocab**: every single
byte plus a few hundred common multi-byte patterns. That still tokenizes any
UTF-8 text, just with lower accuracy than the full file.

`get_qwen_tokenizer(vocab_path=None, force_reload=False)` returns a lazy,
thread-safe global instance. When `vocab_path` is `None` it calls
`_find_vocab_path()`, which searches in this order:

1. PyInstaller temp dir (one-file builds).
2. The executable/module directory.
3. The module directory (`qwen.tiktoken` in the repo).
4. The user cache: `~/.cache/berserker/qwen.tiktoken`.

`TokenCounter.QWEN_VOCAB_PATH` points at the user cache path too. If the full
vocab is missing, the module logs a warning and falls back to the embedded
minimal vocab.

### 2.5 Language-aware fallback (`session/fallback_estimator.py`)

When tiktoken is unavailable (or fails), counting falls back to
`estimate_tokens(text)`, which is **language-aware**. It detects the dominant
text type and applies a characters-per-token ratio:

| Detected type | Chars per token |
|---|---|
| Chinese (CJK-heavy, also used for Japanese/Korean) | 1.8 |
| English (Latin) | 4.0 |
| Code (keywords + structure heuristics) | 3.5 |
| Mixed (10 to 30% CJK) | 2.8 |

The result is `max(1, int(len(text) / ratio))`. This is not the naive
`len(text) // 4` that older docs claimed. `FallbackTokenEstimator.estimate`
and the module-level `estimate_tokens` are equivalent; `estimate_with_language`
returns `(tokens, detected_language)` for debugging.

> Note: `tool/truncate.py` has its own small `count_tokens()` that uses
> tiktoken's `cl100k_base` and, on failure, a plain `len(text) // 4` estimate.
> That one is character-based, not language-aware. It is used by pruning and
> compaction and is a rough approximation.

### 2.6 Benchmark script (`session/benchmark_tokenizer.py`)

A standalone script that measures vocab loading time, tokenizer init time, and
counting throughput across text sizes. Run it with `python -m
berserker.session.benchmark_tokenizer`. It is a development tool, not part of
the runtime path.

---

## 3. Context windowing and model metadata

The executor never hardcodes per-model context windows in the agent layer.
Instead it asks the provider registry:

```python
model_metadata = provider_registry.get_model_metadata(model_name)
context_limit = model_metadata.get("context_window", _DEFAULT_CONTEXT_LIMIT)  # 128000
compaction_buffer = model_metadata.get("compaction_buffer", _COMPACTION_BUFFER)  # 20000
```

`provider_registry.get_model_metadata` returns registered metadata
(`context_window`, `compaction_buffer`, provider, and so on) and falls back to
sensible defaults for unknown models. The fallback defaults live in
`agent/constants.py`:

```python
_DEFAULT_CONTEXT_LIMIT = 128000
_COMPACTION_BUFFER = 20000
```

`agent/manager.py` still defines `_MODEL_CONTEXT_LIMITS` and
`_MODEL_COMPACTION_BUFFERS` dicts, but **nothing references them**. They are
dead code. Do not rely on their contents; the values that actually drive
behavior come from `provider_registry.get_model_metadata` plus the constants
above.

The decision helper is `CompactionStrategy` (see section 8):

```python
strategy = CompactionStrategy(compaction_config)
if strategy.should_compact(total_tokens, context_limit):
    # total_tokens > min(context_limit * compact_threshold,
    #                     context_limit - strategy.config.reserved)
```

`get_compact_threshold(model_limit)` returns `int(model_limit *
compact_threshold)`; it is available on the strategy but the executor's
auto-trigger path uses `should_compact`.

---

## 4. Dynamic tool-output truncation

### 4.1 `tool/truncate.py`

Constants:

```python
DEFAULT_MAX_TOKENS = 4096
DEFAULT_COMPACTION_BUFFER = 20000
TRUNCATION_MARKER = "\n\n[Output truncated: exceeded {max} tokens]"
```

The public functions:

```python
count_tokens(text)            # tiktoken cl100k_base, else len(text) // 4
truncate_output(text, max_tokens=None)   # -> (text, was_truncated)
truncate_result(result, max_tokens=None) # -> result dict, output truncated
calculate_dynamic_max_tokens(context_info, default_max, default_buffer)  # -> int
```

`truncate_output` takes **three** arguments (there is no `truncate_to_token_limit`):

```python
def truncate_output(text, max_tokens=None):
    # type: (str, Optional[int]) -> Tuple[str, bool]
```

Behavior:

1. If `max_tokens is None`, use `DEFAULT_MAX_TOKENS` (4096). Empty input
   returns `("", False)`.
2. Count the whole text; if it fits, return it unchanged.
3. Reserve room for the truncation marker, then **binary search on character
   position** to find the largest prefix that still fits `max_tokens` minus the
   marker's own tokens.
4. Trim the cut to the last newline when that leaves a sane prefix.
5. Append `TRUNCATION_MARKER` and return `(result, True)`.

The marker is a module constant and the message includes the limit:
`"\n\n[Output truncated: exceeded {max} tokens]"`.

`calculate_dynamic_max_tokens`:

```python
def calculate_dynamic_max_tokens(context_info, default_max, default_buffer):
    # type: (Optional[Dict[str, Any]], int, int) -> int
```

- `context_info is None` → `default_max`.
- Otherwise `available = context_limit - current_tokens - compaction_buffer`,
  returning `max(available, default_max)`. So tools always get at least the
  configured default, and more when the window has spare room.

### 4.2 How the context_info flows to tools

The chain is:

```
AgentExecutor.execute()                        (agent/executor.py)
  ├─ for each tool call:
  │    total_tokens  = sum of cached content counts over full_messages
  │    context_limit = model_metadata["context_window"]
  │    compaction_buffer = model_metadata["compaction_buffer"]
  │    base_extra = { workspace, on_tool_call,
  │                   "context_info": { current_tokens, context_limit,
  │                                     compaction_buffer } }
  │    tool_ctx = ToolContext(..., extra=base_extra)
  │
  ▼
ToolRegistry.execute(tool_id, args, ctx)       (tool/registry.py)
  ├─ context_info = ctx.extra.get("context_info")
  ├─ base_max     = tool.config.max_output_tokens or registry.max_output_tokens
  ├─ dynamic_max  = calculate_dynamic_max_tokens(context_info, base_max,
  │                                              DEFAULT_COMPACTION_BUFFER)
  └─ result       = truncate_result(result, dynamic_max)
```

Separately, the executor hard-caps tool output at 64,000 characters
(`max_tool_output_chars`, configurable per agent in its options) before it is
appended to the message list, adding a "[Output truncated: N characters
omitted...]" note.

---

## 5. Session layer

### 5.1 `SessionManager` (`session/manager.py`)

`SessionManager` is constructed with a `project_id`:

```python
manager = SessionManager(project_id="default")  # module singleton exists too
```

**Session CRUD**

```python
create(session_id=None, title=None, parent_id=None) -> str
load(session_id) -> Optional[Dict]
list_sessions(limit=50, offset=0) -> List[Dict]          # newest first
delete(session_id) -> bool                               # cascade
update_title(session_id, title) -> bool
list_sessions_by_workspace(workspace_id, limit=50, offset=0) -> List[Dict]
set_workspace(session_id, workspace_id) -> bool
update_project_id(directory) -> None                     # sha256(directory)[:16]
session_exists(session_id) -> bool
```

There is no `create_session`/`get_session`/`delete_session`. The method names
are `create`, `load`, and `delete`. `load` returns a dict with `id`,
`project_id`, `title`, `created_at`, `updated_at`, and the full `messages`
list.

**Message operations**

```python
append_message(session_id, role, content,
               tool_calls=None, tool_result_for=None,
               tool_name=None, reasoning_content=None) -> str
replace_messages(session_id, List[Dict]) -> int
get_messages(session_id) -> List[Dict]
get_messages_window(session_id, limit, offset_from_end=0) -> List[Dict]
get_message_count(session_id) -> int
delete_message(session_id, message_id) -> None
```

Notes:

- `append_message` raises `ValueError` if the session does not belong to the
  manager's project. It stores `role` and everything else inside the `data`
  JSON column, then publishes `MESSAGE_ADDED`.
- There is no `tool_call_id` or `name` kwarg. Tool results are linked with
  `tool_result_for` (the tool call's id) and `tool_name`.
- `replace_messages` runs in a `BEGIN IMMEDIATE` transaction: delete all of the
  session's messages, insert the new list in order, then publish
  `SESSION_COMPACTED`. It returns the number of messages inserted. This is what
  compaction uses to persist the shrunk history. `reasoning_content` and
  `tool_name` survive the round trip.
- `get_messages_window` is the GUI pagination helper: newest-first `LIMIT
  OFFSET`, then reversed to chronological order.

**Per-session agent config**

```python
save_agent_config(session_id, agent_name, model=None) -> None
load_agent_config(session_id) -> Optional[Dict]   # {"agent_name", "model"}
delete_agent_config(session_id) -> None
```

Backed by the `session_agent_config` table (upsert). The executor calls
`save_agent_config` after compaction so the session remembers its agent.

**Active context coordination**

```python
switch_to(session_id) -> SessionContext
cleanup_active_contexts(max_age=3600) -> int
```

`switch_to` aborts the previously active context, clears its instruction
injection state, and returns the `SessionContext` for the requested session.
A module-level `session_manager = SessionManager(project_id="default")`
singleton exists for convenience.

### 5.2 `SessionContext` (`session/context.py`)

`SessionContext` is the runtime face of one session: state machine, abort
coordination, and a lazy message cache. It was missing from the old document.

**State machine.** States are plain class constants on `SessionState`
(`idle`, `active`, `agent_running`, `aborting`, `compacting`). Transitions are
validated against a fixed table:

```
idle <-> active
active -> agent_running
agent_running -> aborting | idle
aborting -> idle
compacting -> idle | active
```

`transition(new_state)` raises `ValueError` on an illegal move and returns
`True` on success.

**Abort coordination.** A `threading.Event` per context:

```python
request_abort()  # sets the event; also transitions AGENT_RUNNING -> ABORTING
is_aborted()
reset_abort()
```

**Message cache.** Messages are loaded lazily from the manager and cached until
invalidated:

```python
get_messages()                    # reloads if dirty
append_message(role, content, **kwargs)   # passes kwargs through to manager
refresh()                         # mark cache dirty
```

**Lifecycle:**

```python
switch_out()   # abort signal + force to IDLE (leaving the session)
switch_in()    # clear abort, transition to ACTIVE (idempotent)
compact(system_prompt, summary)  # replace all messages with
                                 # [system, assistant summary] via replace_messages
close()        # IDLE + abort event set
```

`compact` here is the session-level convenience: it writes a system prompt plus
an assistant summary and persists through the manager. The heavier
AI-summarizing compaction lives in the agent layer (section 8).

**LLM message building:**

```python
build_messages_for_llm() -> List[Dict]
```

Filters messages that should not reach the provider: entries with no role, no
content and no tool data, and empty tool messages. It keeps assistant messages
that only carry `tool_calls` and tool messages that carry `tool_result_for`,
because DeepSeek requires the pairing.

### 5.3 Message persistence format

A message row in the `message` table stores everything in the `data` JSON
column:

```json
{
  "id": "msg_...",
  "session_id": "ses_...",
  "role": "assistant",
  "content": "...",
  "tool_calls": null,
  "tool_result_for": null,
  "created_at": 1730000000,
  "tool_name": "bash",
  "reasoning_content": "..."
}
```

`tool_name` appears on tool messages; `reasoning_content` appears on assistant
messages only.

---

## 6. Message model and helpers

### 6.1 Part system (`session/parts.py`)

There is **no `PartType` enum**. The part model is a set of plain dataclasses
with a `type: str` discriminator:

| Part | Fields |
|---|---|
| `TextPart` | `type`, `id`, `text`, `metadata` |
| `ToolPart` | `type`, `id`, `tool`, `output`, `tool_call_id`, `metadata` |
| `FilePart` | `type`, `id`, `file_path`, `content`, `metadata` |
| `CompactionPart` | `type`, `id`, `summary`, `tokens_before`, `tokens_after`, `metadata` |
| `SnapshotPart` | `type`, `id`, `snapshot_id`, `additions`, `deletions`, `files`, `metadata` |

`MessageV2` composes parts: `id`, `role`, `parts`, `tokens`, `summary`,
`created_at`.

Conversion functions:

```python
parts_to_chat_messages(msg)       # MessageV2 -> List[ChatMessage]
chat_messages_to_parts(messages)  # List[ChatMessage] -> List[MessageV2]
```

Important, corrected facts:

- `parts_to_chat_messages` takes a **single `MessageV2`** and returns a list.
- A `ToolPart` **always** becomes a `ChatMessage(role="tool")` with `output`
  as content and `tool_call_id` set. There is no "pending assistant tool_call"
  logic.
- `CompactionPart` and `SnapshotPart` convert to `role="system"` messages; the
  snapshot becomes the literal string `"[Snapshot: N additions, M deletions,
  K files]"`.
- `chat_messages_to_parts` groups consecutive messages by role and applies
  heuristics (tool role to `ToolPart`, `[Snapshot: ...]` content back to
  `SnapshotPart`, everything else to `TextPart`).

**The `part` table is never written by any production code.** The schema
defines the table, and the conversion functions exist, but no agent/executor
path inserts part rows. Compaction persists through `replace_messages`, not
through parts.

### 6.2 Message repair (`session/message_utils.py`)

`chat_messages_from_session(messages, filter_empty=False)` converts session
message dicts to `ChatMessage` and **repairs tool-call pairing**, which the
chat API requires (an assistant `tool_calls` message must be answered by tool
messages):

- Unanswered `tool_calls` get a synthetic placeholder tool message
  `"[Tool call interrupted — no result was recorded]"` (note: this code string
  contains an em dash).
- Orphan tool messages (no matching pending call) are dropped.

This is the shared loader used by CLI and GUI flows.

### 6.3 Instruction loading (`session/instruction.py`)

`InstructionLoader` discovers `AGENTS.md`, `CLAUDE.md`, or `CONTEXT.md`
(deprecated) files:

- Upward search from the working directory to the filesystem root.
- A global file in the config directory (`~/.config/berserker/AGENTS.md`).
- Content capped at 10 KB with a warning and truncation note.
- mtime-based caching, per-message claim tracking to avoid re-injection, and
  per-session injection tracking (`mark_session_injected`, `clear_session`).
- `load_instructions(workdir=None, session_id=None)` returns formatted
  `"Instructions from: {path}\n{content}"` strings.

The executor injects these for primary agents (plus a "do not repeat them"
notice) and appends the workspace path to the system prompt. Stale system
messages are stripped from history before the fresh system prompt is built, so
instructions do not accumulate across compaction cycles.

### 6.4 Session titles (`session/title_gen.py`)

`SessionTitleGenerator` generates titles from the first user message using the
hidden `title` agent:

- Reuses an existing title if present.
- Falls back to `"Conversation {short_id}"` when there are no user messages or
  the agent fails (2 retries, 1 s apart).
- Truncates to `MAX_TITLE_LENGTH` (50) characters and persists via
  `update_title`.
- `generate_title_async` runs it on a daemon thread.
- `trigger_auto_title(session_id)` is the module-level entry point that guards
  against duplicate triggers per session.

### 6.5 Command history (`session/history.py`)

`CommandHistory` persists per-session slash-command history to a
`command_history` table that it auto-creates at import time (`add`, `get`,
`clear`). This table is outside the main schema list.

---

## 7. Storage

### 7.1 Database layer (`storage/db.py`)

The `Database` class wraps a single SQLite connection per thread with
dict-like row access:

- Database file: `{BERSERKER_DATA_DIR or data_dir}/berserker.db`.
- PRAGMAs on every connection: WAL, `busy_timeout=5000`, `cache_size=-64000`,
  `foreign_keys=ON`.
- Transient "database is locked" errors are retried with a short backoff.
- `execute`, `fetchone`, `fetchall`, `commit`, `rollback`, `tables`,
  `init_schema`, context-manager support.
- `get_db()` returns a thread-safe singleton that auto-initializes the schema;
  `db_context()` yields a fresh instance and closes it on exit.
- Connections are tracked and closed at interpreter exit.

### 7.2 The 15 tables (`storage/schema.py`)

The schema defines exactly these 15 tables (plus `command_history`, created by
`session/history.py` at import time):

| Table | Purpose |
|---|---|
| `account` | User account profiles |
| `account_state` | Per-account state (JSON), FK to account |
| `project` | Project rows; sessions hang off these |
| `session` | Session metadata (columns below) |
| `message` | Messages; body lives in the `data` JSON column |
| `part` | Fine-grained content parts (defined, never written by production code) |
| `todo` | Session todo list, PK `(session_id, position)` |
| `permission` | Per-project permission data (JSON), PK `project_id` |
| `session_share` | Shared session URLs |
| `workspace` | Workspace definitions |
| `migration` | Applied migration versions |
| `session_snapshots` | Git snapshot statistics per session |
| `pruning_state` | Pruning records per session |
| `session_agent_config` | Per-session agent/model override |
| `message_archive` | Recoverable pre-compaction history (archived before every replace; see §8.4) |

Tables that older docs claimed existed (for example `session_tags`, `tag`,
`agent_run`, `tool_call`, `file_change`, `skill_usage`, `compaction_record`,
`setting`, `migration_history`) **do not exist**.

### 7.3 `session` table columns

There is no `agent`, `model`, `status`, `summary`, or `time_created` column.
The actual columns are:

```
id, project_id, workspace_id, parent_id, slug, directory, title,
version, share_url,
summary_additions, summary_deletions, summary_files, summary_diffs (JSON),
revert (JSON), permission (JSON),
created_at, updated_at, time_compacting, time_archived
```

`parent_id` supports hierarchical (child) sessions, but `SessionManager.create`
does not cascade-delete children; `project_id` FK cascades.

### 7.4 `message` and `part` columns

- `message(id, session_id, data JSON, created_at, updated_at)`. There is **no
  `role` column**; the role is inside `data`.
- `part(id, message_id, session_id, data JSON, created_at, updated_at)`. There
  is **no `type` column**; the discriminator lives inside `data`.

### 7.5 Migrations

`storage/migrations.py` applies numbered SQL files from
`storage/migrations/`:

- `001_initial.sql`
- `002_memory_management.sql` (adds `session_snapshots` and `pruning_state`)
- `003_session_agent_config.sql` (adds `session_agent_config`)

`migration_status` reports applied versus pending versions.

---

## 8. Compaction, pruning, and snapshots

### 8.1 Configuration (`agent/compaction.py`)

```python
@dataclass
class CompactionConfig:
    auto: bool = True          # auto-trigger compaction
    prune: bool = True         # enable pruning of old tool outputs
    reserved: int = 20000      # tokens to leave free for the next response
    prune_protect: int = 40000 # protect the last N tokens of tool output
    prune_minimum: int = 20000 # minimum prunable tokens before pruning runs
    compact_threshold: float = 0.8  # fraction of the context window
    keep_last_turns: int = 2     # recent turns kept verbatim during compaction
```

Two corrections vs. the old doc: `prune_minimum` defaults to **20000** (not
10000), and `compact_threshold` is a **float fraction of the context window**
(`int(model_limit * compact_threshold)`), not a fixed integer of 80000.

Helpers:

```python
default_config()                     # -> CompactionConfig()
load_compaction_config(config_dict)  # reads config_dict.get("compaction")
CompactionStrategy(config=None)
strategy.should_compact(tokens, model_limit)   # tokens > min(limit*compact_threshold, limit-reserved)
strategy.should_prune(total_prunable_tokens)   # prune AND total > prune_minimum
strategy.get_compact_threshold(model_limit)    # int(model_limit * compact_threshold)
strategy.get_prune_protect()                   # config.prune_protect
```

`CompactionConfig.from_dict` accepts only the known keys and ignores the rest.
The AgentManager loads it in `load_from_config` / `load_from_config_dict` /
`load_from_config` via `load_compaction_config(config)`.

### 8.2 Pruning (`AgentManager.prune_messages`)

```python
def prune_messages(self, messages, session_id=None):
    # type: (List[ChatMessage], Optional[str]) -> List[ChatMessage]
```

1. Load already-pruned message ids from the `pruning_state` table for the
   session, so nothing gets pruned twice.
2. First pass over the messages: collect tool-role messages that are not yet
   `_pruned`, **skipping `skill` outputs entirely**, and total their tokens
   with `count_tokens`.
3. If `CompactionStrategy.should_prune(total)` fails (pruning disabled, or the
   prunable total is below `prune_minimum`), return the messages untouched.
4. Second pass, walking backward from the newest tool message: keep anything
   inside the `prune_protect` token window; beyond it, replace the content with
   `"[Tool output pruned to save context]"`, preserving `tool_call_id` and
   `name`, and mark the new message `_pruned = True`.
5. With a `session_id`, persist each pruned record to `pruning_state`
   (`message_id` = `md5(content)[:12]`, `original_content_hash` =
   `sha256(content)[:16]`, lengths, timestamp).
6. With a `session_id` and at least one prune, publish `PRUNING_COMPLETED`.

### 8.3 AI compaction (`AgentManager.compact`)

```python
def compact(self, messages, max_tokens=4096, abort_event=None, model="gpt-4o"):
    # type: (List[ChatMessage], int, Any, str) -> List[ChatMessage]
```

Hybrid strategy: summarize the older turns, keep the recent ones verbatim.

1. Estimate total tokens with `_msg_token_count(msg, model)`, which delegates
   to `TokenCounter.count_messages([msg], model)` — the **same counter the
   trigger uses** (per-message overhead plus language-aware content, tool-call
   argument, and `reasoning_content` counts). It used to go through
   `tool.truncate.count_tokens` (naive `len(text) // 4` without tiktoken),
   which read lower than the trigger and could make `compact()` judge an
   over-limit history "under budget" and return it unchanged (regression
   tests: `tests/test_compaction_gate.py`). If below `max_tokens`, return the
   messages unchanged.
2. Split system messages from the rest. If there are 2 or fewer non-system
   messages, compaction would not help, so return unchanged.
3. Split the non-system messages into turns at each user message
   (`_split_turns`; assistant(tool_calls)/tool groups are never split, so a
   kept tail is always API-safe). The newest `keep_last_turns` turns are kept
   verbatim as the tail, capped at 30% of the budget.
4. The older turns are sent to the hidden `compaction` agent with a user
   prompt asking for a **five-section structured summary** (Goal,
   Instructions, Discoveries, Accomplished, Relevant files / directories).
   The call runs with an empty `ToolRegistry()` (read-only use), session id
   `"compaction-internal"`, and the caller's `abort_event` (so a user abort
   also cancels the summarization pass).
5. The result is `system_messages + [summary_msg] + tail`. The summary uses
   the **assistant role** (not system) to avoid multiple-system-message
   problems with OpenAI.
6. Fallback when the AI call fails or returns empty: an assistant message
   `"[Previous conversation summarized: {n} messages compressed to save
   tokens]"` stands in for the summary.
7. Hard guarantee: if the assembled result still exceeds `max_tokens`, tail
   turns are shed (oldest first) until it fits; if even the summary alone is
   over budget, its text is truncated.

### 8.4 The unified trigger (`AgentExecutor._execute_compaction`)

All three compaction entry points funnel through one method in
`agent/executor.py`:

- **Pre-execution** (`reason="token_overflow"`), when the freshly built message
  list already exceeds the trigger. Runs for primary agents even when
  `auto=False` (mode=primary fallback). Includes snapshot tracking.
- **Loop compaction** (`reason="loop_token_overflow"`), when tokens grow past
  the trigger inside the tool loop. Includes snapshot tracking.
- **Context retry** (`reason="context_retry"`, no snapshot tracking), after a
  `ContextLengthExceeded` from the provider; pruning then compaction run on a
  deep copy, and `ensure_retry_fits` then verifies the payload is actually
  under the target — if not, `_fallback_truncate_messages` force-drops the
  oldest turns, so the retry never resends a byte-identical over-limit
  request (which would re-fail identically).

The method's order of operations:

1. If a `SnapshotTracker` was passed, create a pre-compaction snapshot
   (`git stash create`, working tree untouched). If it has a hash, persist it
   and publish `SNAPSHOT_CREATED` with zeroed stats.
2. Publish `COMPACTION_STARTED` with the reason.
3. Count tokens before (`TokenCounter.count_messages`).
4. Run `prune_messages(...)` then `compact(..., max_tokens=compaction_budget(
   context_limit, strategy.config.reserved, model_name, token_counter),
   abort_event=abort_event, model=model_name)`. `compaction_budget` returns
   `context_limit - reserved` when an exact tokenizer is available (tiktoken,
   or the bundled Qwen BPE vocab); otherwise it multiplies the budget by a
   0.7 safety factor (`_ESTIMATION_SAFETY_FACTOR`), because the fallback
   estimator undercounts dense code/JSON for DeepSeek-class tokenizers by
   ~25-35% (measured: 835,795 estimated vs 1,087,980 API-billed).
5. Count tokens after and compute `tokens_freed`.
6. For pre-execution compaction, if the result still exceeds the target, run
   `_fallback_truncate_messages`: the non-system budget first deducts the
   system message's own token cost (so the re-prepended system prompt cannot
   push the total back over the target), then binary-search how many oldest
   non-system messages to drop (keeping at least the last 5 when possible),
   always preserving the system prompt. The cut point is then aligned to a
   tool-group boundary: a kept suffix starting with tool messages would
   orphan them, so those are dropped too.
7. If a tracker was provided and both hashes exist, diff the two snapshots,
   save the post snapshot with the diff stats, and publish `SNAPSHOT_CREATED`.
8. Publish `COMPACTION_COMPLETED` with `tokens_before`, `tokens_after`,
   `tokens_freed`.
9. Archive the session's current messages via
   `SessionManager.archive_messages(session_id, reason=...)` into the
   `message_archive` table (recoverable pre-compaction history; failure only
   logs a warning), then persist the compacted messages via
   `SessionManager.replace_messages` (no part-table sync happens).
   `list_archives(session_id)` / `get_archive(archive_id)` read them back.
   The manual `/compact` path archives with `reason="manual-compact"`.
10. Persist the agent config via `save_agent_config`.

The auto-trigger checks differ slightly by location:

- **Pre-execution**: `if strategy.config.auto or agent.mode == "primary"`, then
  `if strategy.should_compact(total_tokens, context_limit)`.
- **In-loop**: runs for primary agents only (`if agent.mode == "primary"`),
  then fires on either of two checks against the running token count: the
  same `should_compact` total check, or a per-iteration **increment guard** —
  if the tokens added since the last checkpoint exceed
  `max(prune_protect, 10% of the window)` (`loop_increment_trigger`),
  compaction runs early with `reason="loop_increment_overflow"` and the
  checkpoint resets to the post-compaction count. A single tool result is
  already capped by `max_tool_output_chars` (default 64000); the increment
  guard catches a burst of tool calls within one checkpoint interval.

### 8.5 Snapshots (`session/snapshot.py`)

`SnapshotTracker` captures the working tree without modifying it, using
`git stash create`:

```python
tracker = SnapshotTracker(repo_path=None, db=None)
snap = tracker.create_snapshot(session_id)     # {snapshot_id, hash, session_id, created_at}
diff = tracker.diff_snapshots(old_hash, new_hash)
stats = tracker.get_diff_stats(diff)           # {additions, deletions, files}
tracker.save_snapshot(snap, **stats)           # -> session_snapshots row
tracker.get_session_snapshots(session_id)      # -> list of rows
```

The method names are `create_snapshot` / `save_snapshot` / `diff_snapshots`.
There is no `snapshot_before_compaction` / `snapshot_after_compaction`.
`get_diff_stats` counts `+`/`-` lines (excluding `+++`/`---` headers) and
`diff --git` lines. If git is unavailable or the directory is not a repo,
`create_snapshot` returns a hash-less dict and nothing is persisted.

---

## 9. Events

`EventBus` is a thread-safe publish/subscribe bus (`bus/bus.py`). The
memory-management and session events:

| Event | Payload | Published by |
|---|---|---|
| `session.created` | `{"id", "project_id"}` | `SessionManager.create` |
| `session.deleted` | `{"id", "project_id"}` | `SessionManager.delete` |
| `message.added` | `{"session_id", "message_id"}` | `SessionManager.append_message` |
| `session.compacted` | `{"session_id", "message_count"}` | `SessionManager.replace_messages` |
| `compaction.started` | `CompactionStartedData(session_id, reason)` | `AgentExecutor._execute_compaction` |
| `compaction.completed` | `CompactionCompletedData(session_id, tokens_before, tokens_after, tokens_freed)` | `AgentExecutor._execute_compaction` |
| `pruning.completed` | `PruningCompletedData(session_id, tokens_freed, messages_pruned)` | `AgentManager.prune_messages` |
| `snapshot.created` | `SnapshotCreatedData(session_id, snapshot_id, additions, deletions, files)` | `AgentExecutor._execute_compaction` |

The event name is `MESSAGE_ADDED`, not `MESSAGE_APPENDED`. Compaction, pruning,
and snapshot events come from the agent layer (manager/executor), not from
`SessionManager`. Other bus events exist (`tool.executed`, `config.changed`,
`permission.requested`, `agent.message_sent/received/failed`) but are outside
memory management. Subscribers register with `bus.subscribe(event, callback)`;
one-shot callbacks use `bus.once`.

---

## 10. Configuration reference

### 10.1 Compaction settings

Read from the `"compaction"` key of the config dict by
`load_compaction_config`:

```json
{
  "compaction": {
    "auto": true,
    "prune": true,
    "reserved": 20000,
    "prune_protect": 40000,
    "prune_minimum": 20000,
    "compact_threshold": 0.8,
    "keep_last_turns": 2
  }
}
```

| Key | Type | Default | Meaning |
|---|---|---|---|
| `auto` | bool | `true` | Auto-trigger compaction before/in the tool loop |
| `prune` | bool | `true` | Enable pruning of old tool outputs |
| `reserved` | int | `20000` | Tokens left free for response generation |
| `prune_protect` | int | `40000` | Protect the newest N tokens of tool output |
| `prune_minimum` | int | `20000` | Minimum prunable tokens before pruning activates |
| `compact_threshold` | float | `0.8` | Fraction of the context window used by `get_compact_threshold` |
| `keep_last_turns` | int | `2` | Recent turns kept verbatim during compaction (older turns are summarized) |

Unknown keys are silently ignored.

### 10.2 Agent-level options

Per-agent options in config can carry `max_tool_output_chars` (default 64000),
which hard-caps tool result characters before the token-based truncation runs.
Hidden agents (`title`, `compaction`, `summary`) inherit the primary agent's
model when they are not listed in the config.

### 10.3 Environment variables

Only these environment variables exist. There are **no**
`BERSERKER_COMPACTION_*` or `BERSERKER_SUBAGENT_TIMEOUT` variables.

| Variable | Affects |
|---|---|
| `BERSERKER_CONFIG_DIR` | Config directory (`get_config_dir`) |
| `BERSERKER_DATA_DIR` | Data directory, hence the SQLite database file (`get_data_dir`, `storage/db.py`) |
| `BERSERKER_CACHE_DIR` | Cache directory, including `qwen.tiktoken` lookup (`get_cache_dir`) |
| `BERSERKER_STATE_DIR` | State directory (`get_state_dir`) |
| `BERSERKER_LOG_DIR` | Log directory (`get_log_dir`) |
| `BERSERKER_BIN_DIR` | Binary directory (`get_bin_dir`) |

The Qwen vocab file also lives under the user cache by default:
`~/.cache/berserker/qwen.tiktoken`.

---

## 11. File index

| File | Role |
|---|---|
| `berserker/session/token_counter.py` | `TokenCounter` (plain class), model encoding map, counting chain |
| `berserker/session/qwen_tokenizer.py` | `QwenTokenizer`, pure-Python BPE, `get_qwen_tokenizer`, vocab discovery |
| `berserker/session/fallback_estimator.py` | `estimate_tokens`, language-aware fallback ratios |
| `berserker/session/benchmark_tokenizer.py` | Performance benchmark script |
| `berserker/session/context.py` | `SessionContext`, `SessionState`, state machine, abort, cache |
| `berserker/session/manager.py` | `SessionManager` CRUD, messages, events, `session_manager` singleton |
| `berserker/session/parts.py` | Part dataclasses, `MessageV2`, conversion functions |
| `berserker/session/message_utils.py` | `chat_messages_from_session`, tool-call pairing repair |
| `berserker/session/instruction.py` | `InstructionLoader` for AGENTS.md / CLAUDE.md / CONTEXT.md |
| `berserker/session/title_gen.py` | `SessionTitleGenerator`, `trigger_auto_title` |
| `berserker/session/history.py` | `CommandHistory`, `command_history` table |
| `berserker/session/snapshot.py` | `SnapshotTracker`: git snapshots, diffs, stats, persistence |
| `berserker/tool/truncate.py` | `count_tokens`, `truncate_output`, `truncate_result`, `calculate_dynamic_max_tokens` |
| `berserker/tool/base.py` | `ToolContext`, `ToolResult`, `Tool`, `ToolConfig` |
| `berserker/tool/registry.py` | `ToolRegistry.execute`, dynamic truncation integration |
| `berserker/agent/compaction.py` | `CompactionConfig`, `CompactionStrategy`, config loaders |
| `berserker/agent/constants.py` | `_DEFAULT_CONTEXT_LIMIT` (128000), `_COMPACTION_BUFFER` (20000) |
| `berserker/agent/manager.py` | `AgentManager`: agents, `prune_messages`, `compact` |
| `berserker/agent/executor.py` | `AgentExecutor`: tool loop, context_info, `_execute_compaction`, fallback truncation |
| `berserker/provider/registry.py` | `get_model_metadata` / `register_model_metadata` (context_window, compaction_buffer) |
| `berserker/provider/base.py` | `ChatMessage`, `ChatResponse`, finish-reason constants |
| `berserker/storage/schema.py` | DDL for the 15 tables plus indexes |
| `berserker/storage/db.py` | `Database`, `get_db`, PRAGMAs, lock retry |
| `berserker/storage/crud.py` | `insert`, `insert_many`, `update`, `delete`, `upsert` helpers |
| `berserker/storage/migrations.py` | Migration runner and status |
| `berserker/bus/bus.py` | `EventBus`, event constants, payload dataclasses |
| `berserker/paths/resolver.py` | `get_config_dir`/`get_data_dir`/`get_cache_dir`/`get_state_dir`/`get_log_dir`/`get_bin_dir` |
