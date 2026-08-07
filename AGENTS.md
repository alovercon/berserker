# AGENTS.md — berserker 开发指南

berserker 是一个 Python 3.8 编码智能体（CLI + wxPython GUI），面向 Windows 7+ 兼容（Python 3.8.10 + PyInstaller 5.13.2 锁定）。本文件聚焦"不查会踩坑"的仓库事实与**每次修改后的检查清单**。

## 常用命令

```bash
# 运行
python -m berserker                     # CLI（交互）
python -m berserker.gui_entry           # GUI（注意：没有 "berserker gui" 子命令）
berserker-gui                           # 安装后 GUI 脚本（pyproject [project.gui-scripts]）

# 测试（仓库根 tests/，不是 berserker/tests/）
python -m pytest tests/ -q
python -m pytest tests/agent/test_registry.py -q      # 单文件
python -m pytest tests/agent/ -q                       # 单包

# 语法检查
python -m py_compile <file>             # 单文件；或 python -m compileall berserker

# Lint（重要：pyproject.toml 有 CRLF 解析问题，ruff 默认读配置会失败）
ruff check --isolated --select E,F --ignore E501 <files>

# 构建（PyInstaller）
.\build.ps1                 # CLI + GUI
.\build.ps1 -GuiOnly        # 仅 GUI；另有 -CliOnly / -Package / -Clean / -Test
```

## 每次修改后的检查清单（务必逐项确认）

### 1. 打包配置与构建脚本
- **新增 Python 子包/子模块**（例如新 provider、新 tool、新 cli 命令文件）：必须加入 **`berserker.spec` 和 `berserker-gui.spec` 的 `hiddenimports`** 显式列表，否则 PyInstaller 产物运行时 `ModuleNotFoundError`。
- **新增数据文件**（prompts/*.md、agent 配置、assets/twemoji、logo 等）：加入两个 spec 的 `datas`。`qwen.tiktoken`（2.5MB 词表）必须保留在仓库且已在 datas 中。
- 改完 spec/代码后跑 `.\build.ps1` 验证产物可启动（`dist/berserker.exe` / `dist/berserker-gui.exe`）。

### 2. requirements.txt 与 pyproject.toml
- 新增第三方依赖需**同时**更新两处：
  - `requirements.txt`（核心 + dev 依赖）
  - `pyproject.toml` `[project.dependencies]` 与 `[project.optional-dependencies]`（`gui`=wxPython、`google`、`dev`）
- 记住 Python 3.8 / Windows 7 约束：`tiktoken` 在 win32 需 Rust 编译器，已注释禁用；`jsonschema<4.18.0` 避开 rpds-py；PyInstaller 锁定 `==5.13.2`。别引入破坏这些锁定的版本。

### 3. docs/ 文档
- 改动公开 API / 命令 / 配置格式 / 行为时，同步更新：
  - `docs/*.md`（英文）：`agent-framework.md`、`agent-config-reference.md`、`gui-design.md`、`context-and-memory.md`、`plugin-guide.md`、`tool-security.md`、`llm_finish_reason_reference.md`
  - `docs/用户手册.md`（中文，**唯一中文文档**，保持中文）
  - `docs/demo/*.json`（示例配置，必须能被当前 schema 校验：用真实 agent 名与工具 ID）
- 文档事实必须与代码一致——本仓库历史上有大量文档漂移（旧气泡架构、build→berserker 改名等），改代码优先同步文档。

### 4. 版本与提交
- 版本号唯一来源：`berserker/__init__.py` 的 `__version__`。升级时同步 `docs/用户手册.md` 里的版本说明（无 CHANGELOG）。
- 无 CI、无 pre-commit 钩子：提交前自行跑 `python -m pytest tests/ -q`（排除下述已知坏文件）+ `ruff check --isolated` + 语法检查。
- 提交信息遵循仓库既有风格（如 `feat(...)` / `fix: ...` / `docs: ...` / `chore: ...`，可参考 `git log --oneline`）。

## 架构要点（不查文件容易搞错）

- **内置 agent**：`berserker`（主编程 agent，**原 "build" 已改名**，不要再写 build）、`plan`、`executor`、`general`、`explore`、`consultant`、`critic`、`compaction`、`title`、`summary`。`registry` **没有 `list_hidden()`**，用 `list_by_mode("hidden")`。工单团队（ticket-lead/fixer/qa）已移除，`berserker/agent/configs/` 为空（loader 仍会扫描该目录，可放自定义 agent）。
- **工具 ID**：`read`/`write`/`edit` 参数是 `file_path`（不是 path/offset/limit）；LSP 是**单个 `lsp` 工具**（不存在 `lsp_diagnostics`/`lsp_symbols` 等 ID）；完整工具集见 `berserker/tool/` 与 prompts.py 的 `_ALL_TOOL_IDS`。
- **agent 配置有两条加载路径**：AgentSchema 路径（`validate_agent_schema`/`load_agents_from_config`，严格校验、内置 agent 优先、重名跳过）vs legacy `AgentManager.load_from_config`（支持 `prompt_append`/`system_prompt`/顶层 `tools`，可覆盖内置）。文档与 demo 配置写的是 schema 路径行为。
- **配置位置**：全局 `~/.config/berserker/config.json`；项目 `berserker.json` 或 `.berserker/config.json`。
- **事件总线**：消息事件名是 `MESSAGE_ADDED`（不是 MESSAGE_APPENDED），会话相关有 `SESSION_CREATED/DELETED/COMPACTED`，压缩/裁剪/快照事件由 agent 层发布。
- **GUI 入口/线程**：GUI 实现 `DisplayAdapter` 接口（`berserker/gui/controller.py` 的 GUIController），agent 在 daemon 线程跑、UI 经 `wx.CallAfter` 更新；会话消息分页加载（PAGE_SIZE=20，"Load earlier" 头部增量插入）。
- **存储**：SQLite 14 张表（schema 见 `berserker/storage/schema.py`）；session/message 数据存在 JSON `data` 列；`prune_minimum=20000`、`compact_threshold=0.8`（窗口比例，非绝对 token 数）。

## 工具/环境陷阱

- **SessionContext.switch_out**：仅当状态为 `AGENT_RUNNING`/`ABORTING` 时才设置 abort 事件（IDLE 下是 noop），与 docstring 一致；`request_abort()` 则无条件设置事件。
- **Python 3.8 约束**：不要用 `X | Y` 类型联合、内置泛型（`list[str]`）等 3.10+ 语法；ruff/black 配置 target py38、line-length 100。
- **logo 生成**：`generate_logo.py` 从 `berserker/assets/log.svg`（矢量源，唯一权威）用 PIL + numpy 解析渲染，生成 `logo.png`（512）、`logo-{16..256}.png`、`logo.ico`。改 logo 改 SVG 后重跑它；ICO 用 struct 手动打包多尺寸 PNG 帧（PIL `append_images` 不可靠）。
- **新增 LLM provider**：在 `berserker/provider/` 建模块并在 `provider/registry.py` 注册；OpenAI 兼容服务商继承 `OpenAICompatibleProvider`（无需自实现）。
