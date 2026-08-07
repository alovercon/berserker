# berserker

**开源 AI 编码智能体（Python 移植版）** — 一款强大的自主编程助手：能够规划、编写、编辑、运行命令，并在真实代码库上迭代，支持终端（CLI）与原生 GUI。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)
![Version](https://img.shields.io/badge/version-0.1.0-blue)

> English version: [README_en.md](README_en.md) · 详细中文手册：请查阅 [docs/用户手册.md](docs/用户手册.md)

---

## ✨ 功能特性

- **多智能体编排** — 分层智能体框架，内置 `AgentSchema` 校验、统一注册表，以及 10 个内置智能体：`berserker`（主编程）、`plan`（规划）、`executor`（执行）、`general`（通用）、`explore`（探索）、`consultant`（咨询）、`critic`（评审）、`compaction`（压缩）、`title`（标题）、`summary`（总结）。
- **25+ 内置工具** — 文件操作（`read`、`write`、`edit`、`multiedit`、`apply_patch`）、Shell（`bash`）、搜索（`grep`、`glob`）、`git`、LSP、task/plan/todo、Web 搜索、技能加载与 MCP 集成，支持并行/串行编排。
- **15+ LLM 提供商** — OpenAI、Anthropic、Azure OpenAI、Google Gemini、Amazon Bedrock、Mistral、Groq、xAI、OpenRouter、Perplexity、Together、Cerebras、DeepInfra、Venice、GitLab 以及任意 OpenAI 兼容端点。支持 DeepSeek 思考模式（`reasoning_content`）回传。
- **持久会话与记忆** — SQLite 存储会话、上下文窗口管理、Token 计数（tiktoken / 内置 Qwen 分词器 / 字符回退）、压缩（compaction）、工具输出裁剪与快照追踪。
- **插件系统** — 即插即用，支持生命周期钩子、斜杠命令与可配置行为。
- **细粒度权限** — 每个工具独立 allow/deny/ask 规则，支持会话级"全部允许"。
- **原生 wxPython GUI** — 虚拟化聊天与流式消息气泡、会话侧边栏、聊天内搜索、Markdown 预览对话框、工具权限提示、智能体/模型选择器。
- **事件总线架构** — 解耦、可观测、可测试的核心。

## 🚀 快速开始

```bash
# 1. 克隆并安装
git clone https://github.com/<you>/berserker.git
cd berserker
pip install -r requirements.txt            # 核心（Python 3.8+）
pip install -e ".[gui,google]"              # 可选：GUI + Google 提供商

# 2. 配置你的 LLM 提供商
berserker auth                              # 交互式凭据配置
#   或直接编辑配置文件（见下方"配置"）

# 3. 运行 CLI（交互式聊天）
berserker

# 4. 或启动原生 GUI
berserker-gui
```

## 🖥 使用

### CLI

```bash
berserker                          # 交互式聊天（默认）
berserker run "修复 main.py 中的 bug"   # 一次性、非交互执行
berserker serve                    # 启动 API 服务
berserker session list             # 管理会话
berserker models                   # 列出可用模型
berserker providers                # 列出已配置的提供商
berserker agent                    # 列出 / 切换智能体
berserker mcp                      # MCP 服务管理
berserker -p <项目目录>             # 在指定项目目录运行
berserker -c <配置文件>             # 使用指定配置文件
berserker -v                       # 显示版本
```

### 斜杠命令

在交互式 CLI 或 GUI 聊天中输入：

```
/help          — 列出可用命令
/clear         — 开启全新会话
/agent <名称>   — 切换 / 列出智能体
/skills        — 列出已安装技能
/session <id>  — 切换会话
/compact       — 触发上下文压缩
/model <规格>   — 切换模型
/init [焦点]    — 生成 AGENTS.md
/start-work <计划> — 执行已保存的 .omo/plans 计划
```

### GUI

- **聊天区** — 虚拟化消息气泡（用户 / 助手 / 工具），右键复制 / 删除，双击 Markdown 预览。
- **会话侧边栏** — 创建、切换与删除会话。
- **搜索** — `Ctrl+K` 聊天内消息搜索并高亮。
- **权限提示** — 内联批准 / 拒绝工具调用。

## 🛠 配置

配置分层加载（后面的源覆盖前面的）：

1. `~/.config/berserker/config.json` — 全局凭据与默认值
2. `{项目}/berserker.json` 或 `{项目}/.berserker/config.json` — 项目级设置

配置文件定义 **providers**（API 密钥、base URL、模型）与 **agents**（系统提示词、工具权限、模式）。详见：

- [docs/agent-config-reference.md](docs/agent-config-reference.md) — 完整配置参考
- [docs/demo/](docs/demo/) — 开箱即用的示例配置（minimal、full-featured、multi-provider、enterprise）
- [docs/agent-framework.md](docs/agent-framework.md) — 智能体模式与编排

## 🏗 架构

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

- [docs/agent-framework.md](docs/agent-framework.md) — 智能体框架深入解析
- [docs/context-and-memory.md](docs/context-and-memory.md) — 上下文窗口与内存管理
- [docs/gui-design.md](docs/gui-design.md) — GUI 设计与数据流
- [docs/plugin-guide.md](docs/plugin-guide.md) — 插件系统指南
- [docs/tool-security.md](docs/tool-security.md) — 工具沙箱与权限

## 🔨 构建可执行文件

Windows 7+ 独立可执行文件使用 PyInstaller 构建：

```powershell
.\build.ps1                # 构建 CLI + GUI 可执行文件
.\build.ps1 -Package       # 打包为 ZIP
```

详见 [BUILDING.md](BUILDING.md)（为 Windows 7 兼容锁定 Python 3.8.10 + PyInstaller 5.13.2）。

## 🧪 测试

```bash
pip install -e ".[dev]"
pytest
```

## 📦 环境要求

- Python ≥ 3.8.10（Windows 7 兼容构建工具链）
- 至少一个 LLM 提供商 API 密钥

## 📄 许可证

[MIT](LICENSE)

---

*berserker 是开源 AI 编码智能体概念的 Python 移植版。它是一个独立项目，与 Blizzard Entertainment 无任何关联或背书；"berserker"（狂战士）之名源自传奇的北欧战士。*
