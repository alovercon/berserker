# berserker

**The open-source AI coding agent** — an autonomous coding assistant that runs on Windows 7, capable of planning, writing, editing, running commands, and iterating on real-world codebases. Supports both CLI and native GUI interfaces.

> 中文版本：请参阅 [README.md](README.md) · Detailed user manual (Chinese): [docs/用户手册.md](docs/用户手册.md)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Version](https://img.shields.io/badge/version-0.2.0-blue)

---

## ✨ Features

- **Multi-agent orchestration** — a layered agent framework with `AgentSchema` validation, a central registry, and built-in agents (plan / build / explore / general / executor).
- **25+ built-in tools** — file operations (`read`, `write`, `edit`, `multiedit`, `apply_patch`), shell (`bash`), search (`grep`, `glob`), `git`, LSP, task/plan/todo, web search, skill loading, and MCP integration — with parallel/sequential orchestration.
- **15+ LLM providers** — OpenAI, Anthropic, Azure OpenAI, Google Gemini, Amazon Bedrock, Mistral, Groq, xAI, OpenRouter, Perplexity, Together, Cerebras, DeepInfra, Venice, GitLab, and any OpenAI-compatible endpoint. DeepSeek thinking-mode (`reasoning_content`) round-trip supported.
- **Persistent sessions & memory** — SQLite-backed sessions, context windowing, token counting (tiktoken / bundled Qwen tokenizer / character fallback), compaction, tool-output pruning, and snapshot tracking.
- **Plugin system** — drop-in plugins with lifecycle hooks, slash commands, and configurable behavior.
- **Granular permissions** — per-tool allow/deny/ask rules with session-wide "allow all" support.
- **Native wxPython GUI** — virtualized chat with streaming message bubbles, session sidebar, in-chat search, markdown preview dialogs, tool-permission prompts, and agent/model selectors.
- **Event-bus architecture** — decoupled, observable, and testable core.

## 🚀 Quick start

```bash
# 1. Clone & install
git clone https://github.com/<you>/berserker.git
cd berserker
pip install -r requirements.txt            # core (Python 3.8+)
pip install -e ".[gui,google]"              # optional: GUI + Google provider

# 2. Configure your LLM provider
berserker auth                              # interactive credential setup
#   or edit the config file (see Configuration below)

# 3. Run the CLI (interactive chat)
berserker

# 4. Or launch the native GUI
berserker-gui
```

## 🖥 Usage

### CLI

```bash
berserker                          # interactive chat (default)
berserker run "fix the bug in main.py"   # one-shot, non-interactive
berserker serve                    # start the API server
berserker session list             # manage sessions
berserker models                   # list available models
berserker providers                # list configured providers
berserker agent                    # list / switch agents
berserker mcp                      # MCP server management
berserker -p <project-dir>         # run in a specific project directory
berserker -c <config-file>         # use a specific config file
berserker -v                       # show version
```

### Slash commands

Inside the interactive CLI or GUI chat:

```
/help          — list available commands
/clear         — start a fresh session
/agent <name>  — switch / list agents
/skills        — list installed skills
/session <id>  — switch session
/compact       — trigger context compaction
/model <spec>  — switch model
/init [focus]  — generate AGENTS.md
/start-work <plan> — execute a saved .omo/plans plan
```

### GUI

- **Chat area** — virtualized message bubbles (user / assistant / tool), right-click to copy / delete, double-click for markdown preview.
- **Session sidebar** — create, switch, and delete sessions.
- **Search** — Ctrl+K for in-chat message search with highlight.
- **Permission prompts** — approve/deny tool calls inline.

## 🛠 Configuration

Configuration is layered (later sources override earlier ones):

1. `~/.config/berserker/config.json` — global credentials & defaults
2. `{project}/berserker.json` or `{project}/.berserker/config.json` — per-project settings

The config file defines **providers** (API keys, base URLs, models) and **agents** (system prompts, tool permissions, modes). See:

- [docs/agent-config-reference.md](docs/agent-config-reference.md) — full configuration reference
- [docs/demo/](docs/demo/) — ready-to-use example configs (minimal, full-featured, multi-provider, enterprise)
- [docs/agent-framework.md](docs/agent-framework.md) — agent schema & orchestration

## 🏗 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  CLI (cli_entry)   │   GUI (gui_entry / wxPython)           │
├──────────────────────────────┬──────────────────────────────┤
│  DisplayAdapter (CLI / GUI)  │  Event Bus (bus)             │
├──────────────────────────────┴──────────────────────────────┤
│  Agent framework: manager → registry → schema → factory     │
│  Executor (tool loop, abort, permission, selection)         │
│  Tools: file/bash/search/git/lsp/task/plan/web/mcp          │
│  Providers: OpenAI/Anthropic/Gemini/Azure/Bedrock/...       │
│  Session: context, token counting, memory, compaction       │
│  Storage: SQLite │ Plugins │ Permissions │ Commands         │
└─────────────────────────────────────────────────────────────┘
```

- [docs/agent-framework.md](docs/agent-framework.md) — agent framework deep dive
- [docs/context-and-memory.md](docs/context-and-memory.md) — context windowing & memory management
- [docs/gui-design.md](docs/gui-design.md) — GUI design & data flow
- [docs/plugin-guide.md](docs/plugin-guide.md) — plugin system guide
- [docs/tool-security.md](docs/tool-security.md) — tool sandboxing & permissions

## 🔨 Building executables

Windows 7+ standalone executables are built with PyInstaller:

```powershell
.\build.ps1                # build CLI + GUI executables
.\build.ps1 -Package       # package into a ZIP
```

See [BUILDING.md](BUILDING.md) for details (Python 3.8.10 + PyInstaller 5.13.2 for Windows 7 compatibility).

## 🧪 Testing

```bash
pip install -e ".[dev]"
pytest
```

## 📦 Requirements

- Python ≥ 3.8.10 (Windows 7 compatible build toolchain)
- One or more LLM provider API keys

## 📄 License

[MIT](LICENSE)

---

*berserker is a Python port of the open-source AI coding agent concept. The "berserker" name refers to the legendary Norse warriors.*
