# berserker GUI Design

> This document describes the native wxPython GUI shipped with berserker.
> It supersedes `docs/GUI设计文档.md`, which described an older architecture
> built around a `wx.ScrolledWindow` with one `wx.TextCtrl` bubble per message.
> That layout has been replaced by a virtualized message list (`wx.VListBox`).
>
> Tech stack: wxPython 4.2.x | Python 3.8+ | MVC-style split between view,
> controller, and app wiring.

---

## 1. Overview

The GUI follows the same separation of concerns as the rest of berserker. A thin
view layer renders everything; a controller implements the `DisplayAdapter`
interface so the same agent conversation logic works with both the CLI and the
GUI; an app module wires the two together at startup.

```
+------------------------------------------+
|  berserker GUI                            |
+------------+---------------+--------------+
| View       | Controller    | App / Model  |
| gui/main.py| gui/controller| gui/app.py   |
| gui/sidebar| GUIController | start_gui_app|
| gui/chat_  | SlashCommand- | AgentManager |
| listbox    | Handler       | SessionManager|
| ...        | DisplayAdapter| Providers,    |
|            |               | tools, plugins|
+------------+---------------+--------------+
```

Module responsibilities:

| Module | File | Responsibility |
|--------|------|----------------|
| View | `berserker/gui/main.py` | `PyBerserkerFrame`, all panels, toolbar, status bar, theming |
| View | `berserker/gui/chat_listbox.py` | `ChatListBox` virtualized message list + `ChatMessageData` |
| View | `berserker/gui/sidebar.py` | `SidebarPanel`, session list |
| View | `berserker/gui/agent_status_panel.py` | `AgentStatusPanel`, live agent monitor table |
| View | `berserker/gui/md_to_plain.py` | Markdown-to-plain-text helper (mistune AST + tabulate tables) |
| Controller | `berserker/gui/controller.py` | `GUIController`, `SlashCommandHandler`, `DisplayAdapter` implementation |
| App | `berserker/gui/app.py` | `start_gui_app`, message send/stop handlers, wiring |
| State | `berserker/gui/state.py` | Persisted GUI state (`gui.json`, last workspace) |
| Entry | `berserker/gui_entry.py` | Standalone launcher with `--project/--config/--session` |
| Contract | `berserker/display/adapter.py` | `DisplayAdapter` ABC shared by CLI and GUI |

---

## 2. Entry points

There is **no** `berserker gui` CLI subcommand. The GUI is launched one of two
ways:

- `python -m berserker.gui_entry`
- the `berserker-gui` console script, declared in `pyproject.toml` under
  `[project.gui-scripts]` (`berserker-gui = "berserker.gui_entry:main"`).

### 2.1 Launcher behavior (`gui_entry.py`)

`gui_entry.main()` does the following in order:

1. Verifies the bundled `qwen.tiktoken` vocab is reachable (prints a warning
   and falls back to an embedded minimal vocab when missing).
2. Verifies `wxPython` is importable. If not, prints an install hint and
   returns exit code 1.
3. Parses three optional flags: `--project <dir>`, `--config <path>`,
   `--session <id>`.
4. Resolves the workspace with the following priority:
   1. `--project` (explicit override)
   2. the remembered workspace from GUI state (`get_last_workspace()` in
      `berserker/gui/state.py`). The state lives in `gui.json` under the
      platform state directory resolved by `get_state_dir()` (for example
      `%LOCALAPPDATA%\berserker\state\` on Windows, `~/.local/state/berserker/`
      on Linux, and overridable with `BERSERKER_STATE_DIR`)
   3. a directory picker dialog (`wx.DirDialog`) on first launch. If the user
      cancels, the app exits gracefully with code 0.
5. If `--config` was given, loads that config file; otherwise `None` is passed
   so `start_gui_app` loads the default layered config.
6. Calls `start_gui_app(config=config, session_id=args.session,
   workspace=workspace)`.

The remembered workspace is written back after first-launch selection
(`set_last_workspace`) so the picker only appears once.

---

## 3. Component tree

```
wx.App (created in start_gui_app)
+-- PyBerserkerFrame (wx.Frame, 1050 x 600)
    +-- wx.InfoBar (temporary notifications, e.g. search match count)
    +-- wx.ToolBar
    |   +-- Change Workspace (twemoji folder, 1f4c1)
    |   +-- Clear (twemoji trash, 1f5d1)
    +-- wx.SplitterWindow (outer)
    |   +-- SidebarPanel (left, 18% via SIDEBAR_RATIO)
    |   +-- wx.SplitterWindow (inner)
    |       +-- main_content_panel (left)
    |       |   +-- MessageDisplayPanel (proportion 7)
    |       |   |   +-- "Load earlier messages" link (wx.StaticText, optional)
    |       |   |   +-- ChatListBox (wx.VListBox, virtualized)
    |       |   +-- SearchBarPanel (hidden by default)
    |       |   |   +-- wx.SearchCtrl
    |       |   |   +-- close button ("x")
    |       |   +-- DynamicWidgetPanel (hidden when empty)
    |       |   |   +-- selection widgets (RadioBox OR checkboxes + Select All)
    |       |   |   +-- custom text input + Confirm button
    |       |   |   +-- permission prompt (Allow / Deny / Allow All (Session))
    |       |   +-- InputPanel (proportion 1)
    |       |   |   +-- multiline wx.TextCtrl
    |       |   |   +-- Send / Stop buttons
    |       |   |   +-- scroll-to-bottom "v" button
    |       |   +-- ModelAgentBar
    |       |       +-- Agent combo + Model combo
    |       +-- AgentStatusPanel (right, 30% via AGENT_STATUS_RATIO)
    |           +-- wx.ListCtrl (Agent, Phase, Status, Task, Tokens)
    |           +-- Clear button
    +-- wx.StatusBar (4 fields: [24, -2, -1, 200])
        +-- field 0: wx.ActivityIndicator
```

### 3.1 Layout ratios

The outer splitter places the sidebar on the left and the inner splitter on the
right. `SIDEBAR_RATIO = 0.18` (18% of window width), and
`AGENT_STATUS_RATIO = 0.30` for the right agent status panel, leaving 52% for
the main content.

Sash gravities are set proportionally so resizes keep the ratios:

- outer splitter: `SetSashGravity(SIDEBAR_RATIO)`
- inner splitter: `SetSashGravity(1.0 - AGENT_STATUS_RATIO / (1.0 - SIDEBAR_RATIO))`
  which evaluates to about 0.634 with the defaults.

After the window is shown, `_init_sash_positions()` computes the initial sash
offsets from the real client width via `wx.CallAfter`. Minimum pane sizes are
80 px (outer) and 50 px (inner).

### 3.2 Theme

`_THEME` in `gui/main.py` defines the palette:

| Token | Value | Use |
|-------|-------|-----|
| `accent` | `#007AFF` | send button, links, selection accents |
| `accent_hover` | `#0066E6` | hover state |
| `danger` | `#FF3B30` | stop button while running |
| `disabled` | `#C7C7CC` | disabled buttons |
| `bg_primary` | `#F0F0F0` | main background |
| `bg_sidebar` | `#EBEBF0` | sidebar background |
| `bg_card` | `#FFFFFF` | cards / panels |
| `text_primary` | `#1D1D1F` | primary text |
| `text_secondary` | `#6E6E73` | secondary text |
| `separator` | `#D1D1D6` | borders / separators |

The default font family is `Segoe UI` on Windows, `SF Pro Text` on macOS, and
`Sans` elsewhere (`FONT_FAMILY`).

The window and taskbar icons load from `berserker/assets/logo.ico` through a
`wx.IconBundle`, with a per-size PNG fallback chain. Both source and
PyInstaller-frozen layouts are handled via `sys.frozen` / `sys._MEIPASS`.

### 3.3 Status bar

`CreateStatusBar(4)` with widths `[24, -2, -1, 200]`:

- field 0: fixed 24 px, hosts the `wx.ActivityIndicator` while processing
- field 1: flexible status text (e.g. "Ready", "Processing...", token usage)
- field 2: flexible workspace path
- field 3: fixed 200 px, current agent ("Agent: ...")

During processing the activity indicator is repositioned into field 0's rect
via `set_processing_state`.

---

## 4. Message display

### 4.1 Virtualized list

`MessageDisplayPanel` is a thin `wx.Panel` wrapper around `ChatListBox`, a
subclass of `wx.VListBox`. Only visible rows are measured, laid out, and
drawn, so large histories stay responsive. The old per-message `wx.TextCtrl`
bubble approach is gone.

Each row is one `ChatMessageData`:

```python
@dataclass
class ChatMessageData:
    type: str        # "user" | "assistant" | "tool"
    text: str        # raw markdown (user/assistant) or plain text (tool)
    timestamp: str
    tool_name: str
    message_id: str  # persisted id; "" for live un-persisted messages
    is_group_start: bool
    collapsed: bool  # tool output collapsed by default
```

### 4.2 Bubble rendering

Rows are drawn with `wx.GraphicsContext` for anti-aliased rounded rectangles.

- radius: `_BUBBLE_RADIUS = 14`
- bubble width is capped at 72% of the client width (`max_bubble_width =
  int(cli_width * 0.72)`)
- user bubbles are right-aligned, light green `#D1F4D1` with dark text
  `#1D1D1F` and border `#BBDDBB`
- assistant bubbles are left-aligned, white `#FFFFFF` with text `#1D1D1F`
- tool bubbles are warm gray `#E8E4DF` with text `#6E6E73`, rendered in a
  monospace font (`Consolas`)

A single `_bubble_layout()` helper computes the geometry for both
`OnMeasureItem` and `OnDrawItem`, so measured heights always match what gets
drawn. Heights are cached per row and invalidated on resize (`wx.EVT_SIZE`).

### 4.3 Avatars

Each group-start row draws a 20 px avatar. Twemoji PNGs are lazy-loaded and
cached by `_EmojiManager`:

- user rows use the `user-avatar` emoji
- assistant rows use `bot-avatar`, falling back to `info`
- tool rows use the `tool` emoji

If the emoji manifest or PNG is unavailable, `get_initial_bmp()` renders a
colored circle with an initial letter (`U`, `A`, or `T`) as a fallback.

### 4.4 Grouping

Consecutive messages of the same type form a visual group
(`is_group_start` / `_group_starts` set). Only the first message of a group
shows an avatar and a `HH:MM:SS` timestamp; later rows in the group are
drawn tighter (`_GAP_SAME_GROUP = 3`). Group bookkeeping is rebuilt
(`_rebuild_groups`) after any structural change (batch load, front insert,
delete).

### 4.5 Text layout

`_wrap_text()` wraps plain text to the available bubble width: it splits on
spaces first, then character-breaks long words, and preserves empty lines.
Tool output wraps in a monospace font and is collapsed by default.

### 4.6 Bottom fill and auto-scroll

`wx.VListBox` scrolls in whole rows, so without help the last bubble leaves a
gap above the bottom edge at maximum scroll. Two mechanisms close that gap:

- `_recalc_bottom_fill()` computes the leftover viewport height below the
  trailing rows and folds it into the last item's height.
- `scroll_to_bottom()` computes the flush target row geometrically
  (`_flush_first_row`) instead of trusting wx's approximate scrollbar thumb,
  then re-asserts the position once layout settles (`_adjust_bottom` via
  `wx.CallAfter`).

`add_message()` appends and refreshes, and only auto-scrolls when the view is
already at the bottom (`_is_at_bottom()`). The mouse wheel handler scrolls by
rows and snaps to the flush bottom when the user reaches the end
(`_snap_to_bottom_if_at_end`).

### 4.7 Tool output collapse

Tool bubbles collapse to a preview by default: the first 2000 characters,
capped at 5 displayed lines. When there is more content, a toggle link is
drawn:

- collapsed: "Show more"
- expanded: "Show less (N lines total)"

A left click on a tool row toggles collapse; the context menu offers the same
action. Toggling invalidates that row's cached height and recomputes the
bottom fill.

### 4.8 Context menu

Right-clicking a message opens a `wx.Menu`:

- Copy (`Ctrl+C`) copies that message's text
- Select All (`Ctrl+A`) copies all messages joined by blank lines (VListBox
  rows cannot be text-selected)
- for tool messages: a collapse/expand toggle
- for non-user messages: Delete, which removes the row and, when the message
  has a `message_id`, persists the deletion through the delete callback (see
  section 8.5)

### 4.9 Markdown preview dialog

Double-clicking a message opens a resizable dialog. User and assistant text is
rendered as Markdown: `mistune` (with the table plugin) produces HTML, which is
displayed in `wx.html2.WebView` with a fallback to `wx.html.HtmlWindow` when
WebView is unavailable. Tool messages render as a plain `<pre>` block. The
dialog offers a "copy raw markdown" button and a close button.

### 4.10 Pagination

History loads are windowed (see section 8.4). When older messages exist, a
centered "Load earlier messages (N remaining)" `wx.StaticText` link is inserted
above the list. Clicking it fetches the next batch and prepends it
(`insert_front`) while anchoring the current viewport.

---

## 5. Sidebar

`SidebarPanel` (`gui/sidebar.py`) occupies the left splitter pane, with a
minimum width of 250 px.

- **Header**: a bold workspace label, sourced from `WorkspaceManager.get_by_id`
  (workspace name, else directory basename, else the workspace id). Defaults to
  "No Workspace".
- **SESSIONS section**: a "New Session" button (twemoji `2795` plus sign, or a
  plain `+` button as fallback) and a single-selection `wx.ListBox`.
  Each item renders `title (short_id)` or `(untitled) (short_id)`, where
  `short_id` is the first 8 characters of the session id. The list is populated
  by `session_manager.list_sessions_by_workspace`.
- **Context menu**: right-clicking a session offers "Delete Session". Deletion
  goes through a confirmation dialog owned by the controller.
- During agent execution the session list and New Session button are disabled.

The sidebar never switches sessions on its own. It registers callbacks
(`on_session_selected`, `on_new_session`, `on_delete_session`) that the
controller fills in during wiring.

---

## 6. AgentStatusPanel

`AgentStatusPanel` (`gui/agent_status_panel.py`) sits in the right splitter
pane and shows a live view of `agent_monitor` (the agent event bus in
`berserker/agent/monitor.py`).

- A `wx.ListCtrl` in report mode with 5 columns: Agent, Phase, Status, Task,
  Tokens.
- A "Clear" button calls `agent_monitor.clear_all()`.
- A 1 second `wx.Timer` repopulates the list.
- It also registers as an `agent_monitor` listener, so any agent phase/status
  update triggers an immediate refresh through `wx.CallAfter`.
- Rows are color coded: running = `#E3F2FD`, completed = `#E8F5E9`,
  failed = `#FFEBEE`.
- Task text is truncated to 35 characters; Tokens renders
  `"{iters} iters, {tokens} tok"`.
- Column widths re-proportion on resize (18/14/16/32/20% of usable width).
- `Destroy()` stops the timer and unregisters the listener.

---

## 7. Panels in the main content column

### 7.1 SearchBarPanel

A single-row panel with a `wx.SearchCtrl` and a close button. Ctrl+K shows it
(and focuses it); Esc or the close button hides it. Search input fires on every
keystroke but is debounced (see section 9.3).

### 7.2 DynamicWidgetPanel

One panel serves two interactive modes. It is hidden (zero height) when idle.

**Selection mode** (`show_selection`): a question label plus either

- a `wx.RadioBox` for single selection, or
- a list of `wx.CheckBox` items with a "Select All" checkbox for multiple
  selection

Optionally a custom free-text input appears, and a Confirm button collects the
result as `{"selected": [indices...], "custom": "..."}`. If nothing is
selected, the confirm button briefly flashes a hint. The callback signature
matches what `request_selection` returns to the agent.

**Permission mode** (`show_tool_permission`): a bold label
(`"Tool '<name>' requests permission to execute:"`), a read-only list of the
tool arguments (values longer than 100 characters are truncated to 97 plus
"..."), and three buttons:

- Allow (green `#22C55E`)
- Deny (red `#EF4444`)
- Allow All (Session) (blue `#3B82F6`), which sets `_allow_all_session = True`
  and then allows

The `Allow All (Session)` choice is remembered on the frame
(`is_allow_all_session()`), so every later `request_permission` short-circuits
to `True` for the rest of the session.

### 7.3 ModelAgentBar

A horizontal bar with two read-only dropdowns: Agent and Model.

- Switching the agent calls `frame._on_agent_change` which updates the status
  bar and tells the controller to `switch_agent` (which also auto-selects the
  agent's configured model).
- Switching the model calls `controller.switch_model`.
- `set_selected_agent()` deliberately fires the change callback even for
  programmatic selections, so the status bar never drifts from the dropdown.
- Both dropdowns are disabled while an agent is executing and re-enabled when
  the run finishes.

### 7.4 InputPanel

The bottom input area:

- a multiline `wx.TextCtrl` (min height 60 px) with Enter to send and
  Shift+Enter to insert a newline
- Up/Down arrows navigate per-session command history, backed by
  `berserker/session/history.command_history` (loaded on session switch,
  appended on send)
- Ctrl+K opens search, Ctrl+L clears messages (bound via `wx.EVT_CHAR_HOOK`)
- a Send button (accent blue), a Stop button (gray when idle, red `#FF3B30`
  while running), and a small "v" button that scrolls the chat to the bottom

`set_send_enabled()` toggles both the send button and the text control.
Sending clears the input after the message is dispatched.

---

## 8. Controller and thread model

### 8.1 DisplayAdapter

`GUIController` implements `DisplayAdapter` (`berserker/display/adapter.py`),
the same ABC the CLI adapter (`CLIDisplayAdapter`) implements. The agent layer
talks to display-agnostic methods, so the conversation logic never knows
whether it is driving a terminal or a window:

- `display_user_message(text)`
- `display_assistant_message(text)`
- `display_tool_call(tool_name, args, result)`
- `display_token_usage(usage)`
- `display_info(text)`
- `display_error(text)`
- `request_permission(tool_name, args) -> bool`
- `request_selection(question, options, allow_custom, multiple) -> dict | None`
- `request_input(prompt) -> str | None`
- `display_instructions(summary)`
- `display_agent_list(agents, current_agent)`
- `display_skills(skills)`

In the GUI, `display_user_message`, `display_assistant_message`,
`display_tool_call`, `display_token_usage`, `display_info`, and `display_error`
all funnel into the frame's thread-safe wrappers. `request_input` returns
`None`, because interactive input always comes from the main input panel.

### 8.2 Execution flow

`execute_agent()` runs a turn:

1. Under `_state_lock`, it refuses to start if `_is_executing` is already true
   (no concurrent agent runs), clears the abort event, and sets the flag.
2. It disables the model/agent dropdowns and the sidebar session controls.
3. `frame.set_processing_state(True)` shows the activity indicator, enables
   Stop, disables Send, and sets "Processing...".
4. The agent runs in a **daemon thread** (`threading.Thread(..., daemon=True)`),
   calling `agent_manager.execute(...)` with an `on_tool_call` callback, an
   `on_permission_ask` callback, a `selection_callback` in the tool extras,
   and the shared `_abort_event`.
5. When the run finishes, `wx.CallAfter` restores the UI: processing state
   off, selectors and sidebar re-enabled, `_is_executing` cleared (again under
   the lock), and a `_fail_stale_agent` safety net that flips any still-running
   monitor entry to failed.

Every UI touch from the agent thread goes through `wx.CallAfter`. The
`defer_layout=True` variants used for batched history loads are the exception:
they run directly on the main thread.

### 8.3 Blocking interactions

When the agent needs input, the **agent thread** blocks and the **GUI thread**
does not:

```text
agent thread                     GUI thread
request_permission / request_selection
    | reset state, new Event          |
    | show_tool_permission /          |
    |   show_selection  -------------> render prompt
    |                                  |
    | Event.wait()                     user clicks
    |        <----------------------- callback stores result + Event.set()
    | Event returns                    |
    | hide widget ---------------> hide panel
    | return result
```

The same pattern covers permission (`_permission_event`) and selection
(`_selection_event`). `request_permission` first consults the config-driven
permission rules (`permission_checker.check`): `allow` returns `True`
immediately, `deny` returns `False`, and only `ask` (plus not-session-allowed)
reaches the GUI prompt.

### 8.4 Session loading and pagination

`_switch_to_session(session_id)` is the single path for both sidebar selection
and the `/session` slash command:

1. Verifies the session exists.
2. `session_manager.switch_to(session_id)`, updates the controller, frame, and
   `SessionContext`.
3. Clears the chat and loads history with `_load_session_messages`.
4. Enables input and updates the window title to
   `"berserker {version} - Session: {id}"`.

`_load_session_messages` uses windowed SQL reads with `PAGE_SIZE = 20`:

- `get_message_count` for the total
- `get_messages_window(session_id, 20, 0)` for the newest 20
- if older messages remain, the "Load earlier messages" link is added with the
  remaining count
- items are built via `_build_chat_items` (preserving stored timestamps and
  message ids, and mirroring the live tool-call header format) and installed
  with a single batched `set_messages(..., scroll_to_bottom=True)`

Clicking the link calls `_load_earlier_messages`, which fetches the next 20
with `get_messages_window(session_id, 20, remaining_count)`, prepends them via
`insert_front` (viewport-anchored through `ScrollToRow`), and refreshes the
link's remaining count.

### 8.5 Message deletion persistence

`ChatListBox` exposes `set_delete_callback`. The controller wires it to
`_on_message_deleted`, which calls `session_manager.delete_message(session_id,
message_id)` so GUI deletions persist. The row is removed from the in-memory
list regardless, and group bookkeeping plus bottom fill are recomputed.

### 8.6 Abort behavior

Stop sets `_abort_event`. If the run reports `finish_reason == "abort"`, the
controller only sets the status to "Execution stopped". Tool-call bubbles
already displayed are **kept on screen**; they were persisted to the session
during execution, so removing them would desync the view from storage.

### 8.7 Slash commands

`SlashCommandHandler.process(text)` runs before any message is sent. It parses
the input, handles the special exit/start-work cases, looks the command up in
the command registry, and dispatches:

- `/help`, `/clear`, `/agent`, `/skills`, `/session`, `/compact`, `/init`,
  `/model`, `/start-work`
- plugin-registered Python commands (collected from `plugin_manager`)
- Markdown template commands, which validate arguments, resolve the template,
  and return a `"__TEMPLATE__:"`-prefixed string the app layer turns into an
  agent instruction

`/init` returns `"__INIT__"` (optionally `"__INIT__:focus"`), which the app
layer converts into an AGENTS.md generation prompt and executes with the agent.
`/start-work` finds a plan under `.omo/plans/`, switches to the `executor`
agent, and kicks off execution.

---

## 9. Data flows

### 9.1 Sending a message

```text
InputPanel (Enter / Send)
  -> frame._on_send(text)
  -> app._on_send_message(text)
       controller.process_input(text)         # slash command?
         yes: display result; __INIT__ / __TEMPLATE__ become agent runs
         no:  controller.display_user_message(text)  # adds bubble
              _session_context.append_message("user", text)   # persist
              trigger_auto_title(session_id)                 # background
              build_messages_for_llm() -> chat_messages_from_session()
              controller.execute_agent(agent_manager, agent, msgs,
                                       session_id, tool_registry)
                 [daemon thread] agent_manager.execute(...)
                     display_tool_call -> wx.CallAfter -> tool bubble
                     request_permission -> DynamicWidgetPanel prompt
                     request_selection -> DynamicWidgetPanel prompt
                     result -> display_assistant_message (wx.CallAfter)
                 finally -> set_processing_state(False), re-enable UI
```

### 9.2 Session switch

```text
SidebarPanel listbox select
  -> controller.on_sidebar_session_selected(session_id)
       blocked while _is_executing (message instead)
       disable session controls
       _switch_to_session(session_id)
         switch_to(ctx); clear messages
         _load_session_messages: windowed read (20) + load-earlier link
         set_messages(scroll_to_bottom=True)
       re-enable session controls
```

### 9.3 Search

```text
Ctrl+K -> frame._show_search (show SearchBarPanel, focus)
keystroke -> SearchBarPanel._on_search_text -> frame._on_search(query)
  -> wx.CallLater(250, _do_search)            # 250 ms debounce
_do_search -> chat_list.search(query)         # case-insensitive substring
  -> highlights matched lines (#FFF3CD)
  -> InfoBar: "Found N matches" | "No matches found"
Esc / close -> frame._on_search_close: stop timer, clear highlights, dismiss
```

The debounce exists because scanning every message on the main thread for
every keystroke would freeze large histories. The InfoBar is the frame's
notification surface for search feedback, and is dismissed when search closes.

### 9.4 Permission

```text
agent wants to run a tool
  -> permission_checker.check(tool, args)
       ALLOWED -> True (no prompt)
       DENIED  -> False (no prompt)
       ask -> frame.is_allow_all_session()? True : prompt
  prompt: DynamicWidgetPanel.show_tool_permission
  Allow / Allow All (Session) -> callback(True) -> Event.set()
  Deny                        -> callback(False) -> Event.set()
  agent thread resumes with the boolean result
```

### 9.5 Workspace switching

The toolbar's Change Workspace button opens `WorkspaceDialog` (or the picker
path on first launch). `frame.set_workspace(path)` then:

1. Resolves the absolute path and stores it.
2. Syncs the **global** workspace module (`berserker.workspace.set_workspace`)
   so every tool uses the new directory.
3. Refreshes project-scoped slash commands
   (`command_registry.refresh_project_commands`).
4. Updates the status bar field 2 and the window title.
5. Gets/creates the workspace id (`WorkspaceManager.get_or_create`).
6. Tells the session manager which project is active
   (`session_manager.update_project_id`), **before** the sidebar refresh so
   the session list is correct.
7. Clears the message display, resets the controller's session state
   (`controller.reset_session_state`: session id, context, executing flag,
   abort event), and stores the new workspace id on the controller.
8. Disables input with "No Session Selected".
9. Tells the sidebar to show the new workspace's sessions
   (`sidebar_panel.set_workspace(workspace_id)`).
10. Persists the workspace for next launch (`gui/state.set_last_workspace`).
11. Fires the workspace-change callback registered by the app layer, which
    clears the module-level `_current_session_id` / `_session_context`.

---

## 10. App wiring (`gui/app.py`)

`start_gui_app(config, session_id, workspace)` is the composition root. It
runs in order:

1. Sets the global workspace if provided.
2. Loads config if none was passed (default layered config).
3. Configures logging.
4. Loads providers (`provider_registry.load_from_config`), agents
   (`agent_manager.load_from_config`), ticket team configs (patch any
   `gpt-4o` references to the primary model when unavailable),
   permissions (`permission_checker.load_from_config`), and plugins
   (load + `activate_all`).
5. Registers default tools and scans all command sources.
6. Resolves the workspace id for sidebar integration.
7. Creates `wx.App` and `PyBerserkerFrame` with `on_send=_on_send_message` and
   `on_stop=_on_stop_execution`. `show_welcome=False`, because the app shows
   its own state. Input starts disabled: sessions are never auto-selected.
8. Creates `GUIController`, stores it on the frame, registers the workspace
   change callback, populates the agent and model dropdowns (agent list from
   `agent_manager.list_primary`, models from the provider registry).
9. Builds `SidebarPanel`, swaps it in via `frame.set_sidebar`, registers its
   callbacks on the controller, and initializes it with the workspace id.
10. Shows the frame and runs `app.MainLoop()`.

`_on_send_message` first re-syncs `_session_context` to the controller's
current session (session switches/deletions can leave a stale context), then
processes slash commands. For plain messages it displays the user bubble,
persists the message, triggers auto-title generation, builds the LLM message
list, and executes the agent.

`_on_stop_execution` calls `controller.abort_execution()`.

---

## 11. Auto-title generation

On the first user message of a session, `_on_send_message` calls
`trigger_auto_title(session_id)` (`berserker/session/title_gen.py`).

- A module-level set deduplicates attempts per session.
- Sessions that already have a title are skipped.
- `SessionTitleGenerator.generate_title_async` spawns a daemon thread.
- `generate_title` takes the first user message, executes the built-in
  `title` agent (up to 2 retries), truncates the result to
  `MAX_TITLE_LENGTH = 50` characters, and persists it via
  `session_manager.update_title`. If there is no user message or the agent
  fails, it falls back to `"Conversation {short_id}"`.

The sidebar picks up the title on its next refresh, because each row renders
`title (short_id)`.

---

## 12. Key design decisions

- **Virtualized rendering instead of a ScrolledWindow.** `wx.VListBox` only
  measures and draws visible rows. The old per-message `wx.TextCtrl` bubbles
  made every append and resize O(n) with real widget overhead; the new design
  handles tens of thousands of messages with a few visible rows drawn.
- **One geometry source of truth.** `_bubble_layout()` feeds both measurement
  and drawing. Divergence between the two used to cause clipped text and
  oversized bubbles; a single helper eliminates that class of bug.
- **Plain-text wrapping in the list, HTML in the dialog.** The chat rows render
  word-wrapped plain text (fast, cheap to measure). Markdown fidelity is
  available on demand via the double-click preview dialog, which renders
  mistune HTML in `wx.html2.WebView` with an `wx.html.HtmlWindow` fallback.
- **Bottom fill for flush scroll.** Because `wx.VListBox` scrolls in whole
  rows, the newest bubble would otherwise float above the bottom edge. The
  leftover viewport height is folded into the last row's cached height, and
  scroll-to-bottom targets are computed from real geometry rather than the
  approximate scrollbar thumb.
- **Batch mutations for history.** Initial loads use one `SetItemCount` plus
  one `Refresh`; earlier batches are prepended with `insert_front` and an
  anchored scroll position. No per-message layout churn on large histories.
- **wx.CallAfter as the only cross-thread door.** The agent runs on a daemon
  thread; every UI mutation from it goes through `wx.CallAfter`. Blocking
  requests (permission, selection) use `threading.Event` so the GUI thread
  keeps pumping events while the agent thread waits.
- **A shared DisplayAdapter.** The agent layer drives the CLI and GUI through
  the same interface. The GUI controller is a drop-in display backend, and the
  `request_*` methods are the only places where interactive input diverges.
- **Session-wide permission memory.** "Allow All (Session)" is a single flag on
  the frame, consulted before any permission prompt, matching the CLI's
  session-wide allow behavior.
- **Deletion persistence behind a callback.** `ChatListBox` stays storage
  agnostic; the controller injects the `session_manager.delete_message`
  persistence. Live (un-persisted) messages delete locally only.
- **Abort keeps the transcript.** Executed tool bubbles are already persisted,
  so abort leaves them on screen instead of removing them.

---

## 13. File layout

| File | Role |
|------|------|
| `berserker/gui_entry.py` | Standalone launcher (`python -m berserker.gui_entry`, `berserker-gui`) |
| `berserker/gui/app.py` | `start_gui_app`, send/stop handlers, wiring |
| `berserker/gui/main.py` | `PyBerserkerFrame`, all panels, toolbar, status bar, `WorkspaceDialog` |
| `berserker/gui/chat_listbox.py` | `ChatListBox` (`wx.VListBox`), `ChatMessageData`, emoji manager |
| `berserker/gui/controller.py` | `GUIController`, `SlashCommandHandler`, session flow |
| `berserker/gui/sidebar.py` | `SidebarPanel` |
| `berserker/gui/agent_status_panel.py` | `AgentStatusPanel` |
| `berserker/gui/state.py` | Last-workspace persistence (`gui.json`) |
| `berserker/gui/md_to_plain.py` | Markdown to plain text helper (mistune AST, tabulate tables) |
| `berserker/display/adapter.py` | `DisplayAdapter` ABC + `CLIDisplayAdapter` |
| `pyproject.toml` | `[project.gui-scripts]` declares `berserker-gui` |
