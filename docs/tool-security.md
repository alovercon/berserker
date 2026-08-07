# Tool Security Design

## Overview

berserker's tool system wraps LLM-triggered file operations in several layers of protection to stop a bad call from deleting data or damaging the machine. This document covers the workspace boundary check, the dangerous-path patterns, the edit guard, the recursive-delete size guard, the per-tool resource limits, and the permission system that governs bash and the mutation tools.

Two distinct mechanisms are at play:

1. **In-tool path guards** (`_secure_resolve`, `_check_delete_path`, `_check_edit_path`, the rmdir size guard). These live inside the file tool implementations.
2. **A permission layer** enforced by the agent executor. This decides whether a tool call may run at all, and it is the only thing standing between the LLM and the `bash` tool.

---

## Architecture

```
LLM tool call
    │
    ▼
AgentExecutor.execute()  ── permission enforcement
    │                       ├── agent.permission == "full"  → skip checker
    │                       └── permission_checker.check(tool, args, path)
    │                            allow → run
    │                            deny  → "Action denied by permission policy"
    │                            ask   → on_permission_ask(tool, args)
    ▼
ToolRegistry.execute()
    │
    ├── _secure_resolve()  ── workspace boundary check (relative paths)
    │
    ▼
Tool.execute()
    ├── _check_delete_path() ── rm / rmdir
    │   ├── _BLOCKED_PATH_PATTERNS        (system-critical paths)
    │   └── _DANGEROUS_DELETE_PATTERNS    (generated/cache dirs)
    │
    ├── _check_edit_path()   ── edit / multiedit / apply_patch
    │   └── _BLOCKED_EDIT_PATTERNS        (binary / bytecode)
    │
    └── rmdir recursive size guard
        └── estimate > 50 MB → refuse unless dangerous_override
```

### ToolContext.extra and `dangerous_override`

`dangerous_override` is a Context-level bypass flag carried in the `extra` dict of `ToolContext` (defined in `berserker/tool/base.py`). When it is set to `True`, every file-path check short-circuits:

```python
if ctx.extra and ctx.extra.get("dangerous_override"):
    return None  # skip all checks, allow the operation
```

This short-circuit appears in `_check_dangerous_path` (`file_ops.py`), `_check_delete_path` (`file_ops.py`), and `_check_edit_path` (`edit_complex.py`). Because it runs **before** the pattern lists are consulted, it bypasses the blocked patterns as well as the dangerous ones, and it also skips the rmdir recursive size guard. There is no "hard block even with override".

`dangerous_override` is not exposed as a tool parameter and has no user-facing config option. It is an internal bypass intended for code-level injection by the toolchain itself (for example automation or a future trusted-mode UI toggle). For normal LLM calls every check stays active. The supported way for a user to do something the file tools refuse is the `bash` tool, gated by the permission prompt.

### `_secure_resolve` per tool

All file tools resolve their paths through `_secure_resolve(raw_path, workspace)`:

- **Relative paths** are resolved against the **workspace**, not the current directory: `resolved = os.path.abspath(os.path.join(workspace, raw_path))`. If the result lands outside the workspace (the path is not the workspace itself and does not start with `workspace + os.sep`), a `ToolError` is raised: `Path '{}' is outside the workspace '{}'`. This blocks `../../etc/passwd` style traversal.
- **Absolute paths** are allowed through unchanged (see Known Limitations).

The same function is duplicated three times: `berserker/tool/file_ops.py`, `berserker/tool/edit_complex.py`, and `berserker/tool/file_simple.py`. Each module also carries its own `_resolve_workspace(ctx)`, which reads `workspace` from `ctx.extra` and falls back to the current directory. The agent executor injects the workspace into `ToolContext.extra` on every tool call.

`bash.py` has its own variant, `_secure_resolve_cwd`, which applies the same boundary rule to the optional `cwd` argument. It only validates the working directory; the command itself is subject to the permission layer, not to these guards.

### Permission layer

Every agent runs with a `permission` mode. In `berserker/agent/executor.py`, before a tool is executed:

```python
if agent.permission == "full":
    perm_result = ALLOWED
else:
    tool_path = tool_args.get("file_path") or tool_args.get("path")
    perm_result = permission_checker.check(tool_name, args=tool_args, path=tool_path)
```

- `agent.permission == "full"` skips the checker entirely.
- Otherwise `permission_checker.check(tool, args, path)` returns `allowed`, `denied`, or `needs_ask`. Rules are evaluated in order and the first match wins; user-configured rules (loaded from config) are inserted ahead of the defaults. If no rule matches, the result is `needs_ask`.
- `denied` returns an error: `Action denied by permission policy`.
- `needs_ask` calls the `on_permission_ask(tool_name, tool_args)` callback. If no callback is provided, or if the callback throws, the call is **denied by default** (fail-closed).
- `allowed` executes the tool.

The default permission map (`_DEFAULT_PERMISSIONS` in `berserker/permission/__init__.py`):

| Tool | Default |
|---|---|
| `read`, `ls`, `glob`, `grep` | allow |
| `write`, `edit`, `multiedit`, `apply_patch`, `bash`, `task` | ask |
| `mkdir`, `rmdir`, `mv`, `cp`, `rm`, `touch` | ask |

So `bash` is governed by the permission system (default `ask`), never by `_secure_resolve`. How the ask is surfaced depends on the frontend:

- **GUI** (`berserker/gui/controller.py`, `berserker/gui/main.py`): config rules are consulted first (allow/deny return immediately). If the rule says ask, the GUI checks the session-wide "Allow All (Session)" toggle, then shows a dialog with **Allow**, **Deny**, and **Allow All (Session)** buttons.
- **CLI** (`berserker/cli/conversation.py`): prints a permission request and prompts `Allow? [Y/n]:`. Any input other than `n`/`no` grants the call; Ctrl+C or EOF denies it.

---

## `_BLOCKED_PATH_PATTERNS` vs `_DANGEROUS_DELETE_PATTERNS`

Both lists live in `berserker/tool/file_ops.py` and are consumed by `_check_delete_path()`, which checks blocked patterns first and dangerous patterns second. They are **both hard blocks**, and they share the **same** error message:

```
SECURITY: Target path '{}' is protected. This path matched blocked pattern '{}'.
Use bash tool with explicit confirmation if you absolutely need to proceed.
```

The old assumption that the two classes differ in messaging (one hinting at a bypass, the other not) is wrong. They differ in exactly one way: the pattern list they match against.

`_BLOCKED_PATH_PATTERNS` (system-critical paths):

| Category | Matches |
|---|---|
| Windows system dirs | `Windows`, `Windows\System32`, `Windows\SysWOW64`, `Windows\System` (case-insensitive) |
| Program dirs | `Program Files`, `Program Files (x86)`, `ProgramData` |
| Recovery partition | `Recovery` |
| Drive roots | `C:\`, `D:\`, any single drive root |
| Unix system dirs | `/etc`, `/usr`, `/var`, `/bin`, `/sbin`, `/boot`, `/lib`, `/lib64`, `/dev`, `/proc`, `/sys` |
| VCS metadata | `.git`, `.svn`, `.hg` (matched at the end of a path, blocking recursive deletes) |

`_DANGEROUS_DELETE_PATTERNS` (generated / cache directories):

| Matches | Meaning |
|---|---|
| `node_modules` | npm dependency tree |
| `__pycache__` | Python bytecode cache |
| `.venv`, `venv` | virtual environments |
| `.tox` | tox test environments |
| `.mypy_cache`, `.pytest_cache` | type-check / test caches |
| `dist`, `build` | build output |
| `.next`, `.nuxt` | Next.js / Nuxt build output |

Both lists can be bypassed by `dangerous_override`.

---

## Per-tool protections

| Tool | File / class | Protections |
|---|---|---|
| `read` | `file_simple.py` → `ReadTool` | `_secure_resolve`; files over 100 KB are truncated silently to the first 100 KB |
| `write` | `file_simple.py` → `WriteTool` | `_secure_resolve`; content over 1 MB (`_MAX_WRITE_BYTES`) rejected; overwrite warning in output |
| `ls` | `file_simple.py` → `LsTool` | `_secure_resolve`; output capped at 500 entries (`_MAX_LS_ENTRIES`) |
| `glob` | `file_simple.py` → `GlobTool` | root resolved via `_secure_resolve`; results re-filtered to the workspace; capped at 100 matches (`_MAX_GLOB_RESULTS`) |
| `mkdir` | `file_ops.py` → `MkdirTool` | `_secure_resolve` |
| `touch` | `file_ops.py` → `TouchTool` | `_secure_resolve` |
| `mv`, `cp` | `file_ops.py` → `MvTool`, `CpTool` | `_secure_resolve` on source and destination |
| `rm` | `file_ops.py` → `RmTool` | `_secure_resolve`; `_check_delete_path` (blocked + dangerous patterns) |
| `rmdir` | `file_ops.py` → `RmdirTool` | `_secure_resolve`; `_check_delete_path`; recursive size guard |
| `edit` | `edit_complex.py` → `EditTool` | `_secure_resolve`; `_check_edit_path` |
| `multiedit` | `edit_complex.py` → `MultiEditTool` | `_secure_resolve`; `_check_edit_path` |
| `apply_patch` | `edit_complex.py` → `ApplyPatchTool` | `_secure_resolve`; `_check_edit_path` |
| `bash` | `bash.py` → `BashTool` | cwd validated by `_secure_resolve_cwd`; execution governed by the permission layer (default `ask`); 100 KB output truncation; hard timeout |

### Edit guard

`_BLOCKED_EDIT_PATTERNS` (defined in both `file_ops.py` and `edit_complex.py`) rejects text edits to binary and compiled files:

- `.exe`, `.dll`, `.pyd`, `.so`, `.dylib`, `.bin` (binaries, case-insensitive)
- `.pyc`, `.pyo` (Python bytecode)

`_check_edit_path` runs at the top of `EditTool`, `MultiEditTool`, and `ApplyPatchTool.execute()`. Its message is:

```
SECURITY: Editing '{}' is not allowed. This file type is a binary or compiled format
and cannot be safely edited as text. Use bash tool if you absolutely need to modify it.
```

### Recursive delete size guard

`RmdirTool` with `recursive=true` first estimates the directory size with `_estimate_dir_size()`, which walks the tree with `os.walk` and sums `os.path.getsize` over the first **1000 file entries** (it stops there to avoid hanging on enormous trees). If the estimate exceeds `_RMDIR_RECURSIVE_SIZE_WARN` (50 MB), the delete is refused:

```
SECURITY: Recursive delete of '{}' would remove ~{:.1f} MB of data.
Use bash tool with explicit confirmation if this is intended,
or set dangerous_override=true to bypass this guard.
```

The guard is skipped entirely when `dangerous_override` is set.

---

## Permission enforcement flow

1. The executor builds a `ToolContext` whose `extra` includes the workspace and any caller-provided keys.
2. Before running the tool: `agent.permission == "full"` grants everything; otherwise the `PermissionChecker` is consulted with the tool name, the raw args, and the path pulled from `file_path` or `path`.
3. **allowed** → the tool runs.
4. **denied** → the call returns `Action denied by permission policy` without executing.
5. **needs_ask** → `on_permission_ask(tool, args)` is invoked. No callback, or a callback that raises, means deny. The GUI shows Allow / Deny / Allow All (Session); the CLI asks `Allow? [Y/n]:`.
6. Only an affirmative answer executes the tool.

---

## Known limitations

- **Absolute paths are allowed.** `_secure_resolve` (and the bash cwd check) only bounds relative paths. An absolute path such as `C:\Windows\...` passes the boundary check and is then subject only to the pattern / permission layers. This is a recognized P0 gap, not yet fixed.
- **`dangerous_override` bypasses everything**, including the `_BLOCKED_PATH_PATTERNS`. It is a total escape hatch for code that sets it.
- **`_secure_resolve` is duplicated** in three modules (`file_ops.py`, `edit_complex.py`, `file_simple.py`) plus the bash `_secure_resolve_cwd` variant. A fix has to land in four places, and the copies can drift.
- **`bash` is not path-checked.** Its cwd is validated, but the command itself runs whatever it runs once the permission prompt is granted. The default for bash is `ask`, so nothing runs silently by default.
- **`read` truncates silently.** Files over 100 KB come back cut at 100 KB, which can hide content at the end of a file.
- **The recursive size guard is an estimate**, sampled from the first 1000 file entries. Very uneven trees can be misjudged in either direction.
- **`glob` trusts absolute roots.** The results are re-filtered against the workspace, but the root itself may be an absolute path outside it.

---

## Testing

The tool security tests live at the **repo root**, in `tests/`, not under the `berserker` package. Relevant files:

- `tests/test_tool_file_ops.py` — delete protection, dangerous patterns, rmdir size guard
- `tests/test_tool_edit.py` — edit guard
- `tests/test_tool_bash.py` — bash cwd validation and permissions
- `tests/test_tool_file_simple.py` — write size limit, glob/ls truncation
- `tests/test_tool_registry.py` — registry behavior
- `tests/test_tool_base.py` — base classes and context
- `tests/test_tool_todo.py` — todo tool

Run them from the repo root:

```bash
pytest tests/test_tool_*.py
```

Covered scenarios include path-traversal defenses, the bypass flag, blocked vs dangerous patterns, the edit guard, and the recursive-delete size limit.
