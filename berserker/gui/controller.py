"""

berserker.gui.controller — GUI controller that connects agent execution to wxPython UI.



Implements DisplayAdapter interface and manages background agent execution

with thread-safe UI updates via wx.CallAfter.



Python 3.8.10 compatible: uses type comments, no | union syntax.

"""



from __future__ import annotations



import logging
import os
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import wx

from berserker.display.adapter import DisplayAdapter
from berserker.gui.chat_listbox import ChatMessageData
from berserker.gui.main import PyBerserkerFrame
from berserker.agent.monitor import agent_monitor, STATUS_FAILED


if TYPE_CHECKING:

    from berserker.session.context import SessionContext



logger = logging.getLogger(__name__)





# ---------------------------------------------------------------------------

# Slash Command Handler

# ---------------------------------------------------------------------------





class SlashCommandHandler:

    """Process slash commands in GUI mode.



    Supported commands:

    - /help          — Show available commands

    - /clear         — Clear conversation (new session)

    - /agent [name]  — Switch/list agents

    - /skills        — List available skills

    - /session <id>  — Switch session

    - /compact       — Trigger context compaction

    - /init [focus]  — Generate AGENTS.md (sends to agent)

    - /model <spec>  — Switch model

    """



    def __init__(self, controller):

        # type: (GUIController) -> None

        self.controller = controller

        self._plugin_commands = {}  # type: Dict[str, Dict[str, Any]]

        self._load_plugin_commands()

        self._stdout_lock = threading.Lock()



    def _load_plugin_commands(self):

        # type: () -> None

        """Collect plugin-registered commands at initialization."""

        try:

            from berserker.plugin_system import plugin_manager



            self._plugin_commands = plugin_manager.get_registered_commands()

        except Exception as e:

            logger.warning("Failed to load plugin commands: %s", e)

            pass



    def process(self, text):

        # type: (str) -> Tuple[bool, str]

        """Process a slash command.



        Args:

            text: User input text (may start with /).



        Returns:

            (is_command, result_text):

            - is_command=True: text was a command, result_text is display message

            - is_command=False: not a command, pass through to agent

        """

        from berserker.command.processor import (

            CommandResult,

            is_special_command,

            lookup_command,

            parse_command,

        )



        command, arg, _ = parse_command(text)

        if not command:

            return False, ""



        # Special cases that bypass registry

        special = is_special_command(command)

        if special == CommandResult.EXIT:

            return True, "Use the window close button to exit."



        if special == CommandResult.START_WORK:

            return True, self._cmd_start_work(arg)



        # Look up in CommandRegistry

        cmd = lookup_command(command)

        if cmd:

            if cmd.name == "help":

                return True, self._cmd_help()

            elif cmd.name == "clear":

                return True, self._cmd_clear()

            elif cmd.name == "agent":

                return True, self._cmd_agent(arg if arg else None)

            elif cmd.name == "skills":

                return True, self._cmd_skills()

            elif cmd.name == "session":

                return True, self._cmd_session(arg)

            elif cmd.name == "compact":

                return True, self._cmd_compact()

            elif cmd.name == "init":

                return True, self._cmd_init(arg)

            elif cmd.name == "model":

                return True, self._cmd_model(arg)

            else:

                # Legacy plugin command (Python handler)

                if command in self._plugin_commands:

                    return True, self._execute_plugin_command(command, arg)

                # Markdown template command → inject into conversation

                if cmd.template:

                    from berserker.command.formatter import (count_required_args, resolve_command_template, validate_command_args)

                    valid, error = validate_command_args(cmd, arg)

                    if not valid:

                        return True, error

                    resolved = resolve_command_template(cmd, arg)

                    return True, "__TEMPLATE__:" + resolved



        return True, "Unknown command: {}. Type /help for available commands.".format(

            command.lstrip("/")

        )



    def _cmd_help(self):

        # type: () -> str

        from berserker.command.registry import command_registry



        return command_registry.get_help_text()



    def _execute_plugin_command(self, command, arg):

        # type: (str, str) -> str

        """Execute a plugin-registered command.



        Args:

            command: The command name (e.g., '/timer').

            arg: Additional arguments.



        Returns:

            Display message from the plugin command handler.

        """

        cmd_info = self._plugin_commands.get(command)

        if not cmd_info:

            return "Unknown command: {}".format(command)



        handler = cmd_info.get("handler")

        if handler is None:

            return "Plugin command '{}' has no handler.".format(command)



        import io

        import sys



        # Thread-safe stdout capture (Bug 3 fix)

        with self._stdout_lock:

            old_stdout = sys.stdout

            sys.stdout = captured = io.StringIO()

            try:

                result = handler(arg, self.controller.session_id or "")

            except Exception as exc:

                captured.write("Error: {}".format(exc))

                result = None

            finally:

                sys.stdout = old_stdout



        output = captured.getvalue()



        # If handler returned a new session ID, update the controller

        if result and result != self.controller.session_id:

            self.controller.session_id = result

            self.controller.frame.session_id = result

            from berserker import __version__



            self.controller.frame.SetTitle(

                "berserker {} — Session: {}".format(__version__, result)

            )



        if output:

            return output

        return "Command executed successfully."



    def _cmd_clear(self):

        # type: () -> str

        from berserker.session.manager import session_manager



        new_session_id = session_manager.create()

        ctx = session_manager.switch_to(new_session_id)

        self.controller.session_id = new_session_id

        self.controller.frame.session_id = new_session_id

        self.controller.set_session_context(ctx)

        self.controller.frame.clear_messages()

        self.controller.frame.add_assistant_message(

            "Cleared conversation history. New session: {}".format(new_session_id)

        )

        return ""  # Already displayed



    def _cmd_agent(self, agent_name):

        # type: (Optional[str]) -> str

        from berserker.agent.manager import agent_manager



        if not agent_name:  # List available agents

            lines = ["Available agents:"]

            for agent in agent_manager.list():

                current_marker = " (active)" if agent.name == self.controller.current_agent else ""

                lines.append(

                    "  {} — {} [mode: {}]{}".format(

                        agent.name, agent.description or "", agent.mode, current_marker

                    )

                )

                lines.append("    Tools: {}".format(", ".join(agent.tools)))

            lines.append("")

            lines.append("Current agent: {}".format(self.controller.current_agent))

            lines.append("Usage: /agent <name>  (e.g., /agent plan)")

            return "\n".join(lines)



        # Switch agent

        try:

            agent = agent_manager.get(agent_name)

            self.controller.current_agent = agent.name

            return "Switched to agent: {} ({})\n  Mode: {}\n  Tools: {}".format(

                agent.name, agent.description or "", agent.mode, ", ".join(agent.tools)

            )

        except KeyError:

            return "Error: Agent '{}' not found.\nAvailable agents: {}".format(

                agent_name, ", ".join(sorted([a.name for a in agent_manager.list()]))

            )



    def _cmd_skills(self):

        # type: () -> str

        from berserker.tool.skill import _list_available_skills



        skills = _list_available_skills()

        if not skills:

            return (

                "No skills available.\n"

                "Skills can be installed in:\n"

                "  - Local project: .berserker/skills/\n"

                "  - Global config: ~/.config/berserker/skills/"

            )



        lines = ["Available skills:"]

        for skill in skills:

            name = skill.get("name", "unknown")

            desc = skill.get("description", "")

            if desc:

                lines.append("  {} — {}".format(name, desc))

            else:

                lines.append("  {}".format(name))

        return "\n".join(lines)



    def _cmd_session(self, session_id):
        # type: (str) -> str
        from berserker.session.manager import session_manager

        if not session_id:
            return "Error: Please specify a session ID. Usage: /session <id>"

        if not session_manager.session_exists(session_id):
            return "Error: Session '{}' not found.".format(session_id)

        # Switch and load the session's message history into the chat area
        self.controller._switch_to_session(session_id)
        return ""  # History is already displayed


    def _cmd_compact(self):

        # type: () -> str

        """Trigger context compression using LLM-based summarization (parity with CLI)."""

        from berserker.agent.manager import agent_manager

        from berserker.provider.base import ChatMessage

        from berserker.session.token_counter import TokenCounter

        from berserker.storage import get_db



        if self.controller.session_id is None:

            return "Error: No active session."



        session_id = self.controller.session_id



        # Get messages from active context or fallback to manager

        ctx = self.controller.get_session_context()

        if ctx is not None:

            messages = ctx.get_messages()

        else:

            from berserker.session.manager import session_manager



            messages = session_manager.get_messages(session_id)



        if not messages:

            return "No messages to compact."



        # Convert to ChatMessage objects

        chat_messages = []  # type: List[ChatMessage]

        for msg in messages:

            chat_messages.append(

                ChatMessage(

                    role=msg.get("role", "user"),

                    content=msg.get("content", ""),

                    tool_calls=msg.get("tool_calls"),

                    tool_call_id=msg.get("tool_call_id") or msg.get("tool_result_for"),

                )

            )



        # Count tokens before compaction

        token_counter = TokenCounter()

        tokens_before = token_counter.count_messages(chat_messages, model="gpt-4o")



        try:

            compacted = agent_manager.compact(chat_messages)

        except Exception as exc:

            return "Error during compaction: {}".format(exc)



        # Persist compacted messages if reduction occurred

        if len(compacted) < len(chat_messages):

            # Convert ChatMessage objects to dicts for replace_messages

            compacted_dicts = []

            for msg in compacted:

                msg_dict = {

                    "role": msg.role,

                    "content": msg.content,

                    "tool_calls": msg.tool_calls,

                    "tool_result_for": msg.tool_call_id,

                }

                compacted_dicts.append(msg_dict)



            # Persist via context or manager

            if ctx is not None:

                inserted = (

                    ctx._manager.replace_messages(session_id, compacted_dicts)

                    if ctx._manager

                    else 0

                )

                ctx.refresh()

            else:

                from berserker.session.manager import session_manager



                inserted = session_manager.replace_messages(session_id, compacted_dicts)



            # Count tokens after compaction

            tokens_after = token_counter.count_messages(compacted, model="gpt-4o")

            tokens_freed = tokens_before - tokens_after



            # Get pruning stats

            db = get_db()

            pruning_cursor = db.execute(

                "SELECT COUNT(*) as pruned_count, SUM(original_content_length - pruned_content_length) as tokens_freed FROM pruning_state WHERE session_id = ?",

                [session_id],

            )

            pruning_row = pruning_cursor.fetchone()

            messages_pruned = pruning_row[0] if pruning_row and pruning_row[0] else 0

            pruning_tokens_freed = pruning_row[1] if pruning_row and pruning_row[1] else 0



            # Get snapshot stats

            snapshot_cursor = db.execute(

                "SELECT COUNT(*) as snapshot_count, SUM(additions) as total_additions, SUM(deletions) as total_deletions, SUM(files) as total_files FROM session_snapshots WHERE session_id = ?",

                [session_id],

            )

            snapshot_row = snapshot_cursor.fetchone()

            snapshots_created = snapshot_row[0] if snapshot_row and snapshot_row[0] else 0

            total_additions = snapshot_row[1] if snapshot_row and snapshot_row[1] else 0

            total_deletions = snapshot_row[2] if snapshot_row and snapshot_row[2] else 0

            total_files = snapshot_row[3] if snapshot_row and snapshot_row[3] else 0



            # Build stats report

            lines = [

                "Context compressed: {} messages -> {} messages ({} persisted)".format(

                    len(chat_messages), len(compacted), inserted

                ),

                "Tokens: {} -> {} (freed {} tokens)".format(

                    tokens_before, tokens_after, tokens_freed

                ),

                "Pruning: {} messages pruned, {} tokens freed".format(

                    messages_pruned, pruning_tokens_freed

                ),

                "Snapshots: {} created, {} files changed (+{}/-{} lines)".format(

                    snapshots_created, total_files, total_additions, total_deletions

                ),

            ]

            return "\n".join(lines)

        else:

            return "Context unchanged: {} messages (within token budget)".format(len(chat_messages))



    def _cmd_init(self, focus):  # type: (str) -> str

        """Return a special message that triggers AGENTS.md generation."""

        if focus:

            return "__INIT__:" + focus

        return "__INIT__"



    def _cmd_model(self, model_spec):

        # type: (str) -> str

        if not model_spec:

            return "Error: Please specify a model. Usage: /model <provider/model>"



        from berserker.provider.base import ModelNotFoundError, ProviderError

        from berserker.provider.registry import registry as provider_registry



        try:

            provider, model_name = provider_registry.get_provider_for_model(model_spec)

            self.controller.current_model = model_spec

            return "Switched to model: {} (provider: {})".format(model_name, provider.id)

        except (ModelNotFoundError, ProviderError) as exc:

            return "Warning: Model '{}' not found. Will attempt to use it.".format(model_spec)



    def _find_plan(self, plan_name):

        # type: (str) -> Optional[str]

        """Find a plan file by name in .omo/plans/ directory."""

        from berserker.workspace import get_workspace



        workspace = get_workspace()

        plans_dir = os.path.join(workspace, ".omo", "plans")



        if not os.path.isdir(plans_dir):

            return None



        if plan_name.endswith(".md"):

            candidate = os.path.join(plans_dir, plan_name)

            if os.path.isfile(candidate):

                return candidate

        else:

            candidate = os.path.join(plans_dir, plan_name + ".md")

            if os.path.isfile(candidate):

                return candidate



        for fname in os.listdir(plans_dir):

            if fname.endswith(".md") and plan_name.lower() in fname.lower():

                return os.path.join(plans_dir, fname)



        return None



    def _cmd_start_work(self, plan_name):

        # type: (str) -> str

        """Start executing a plan via the executor agent."""

        import os



        if not plan_name:

            from berserker.workspace import get_workspace



            workspace = get_workspace()

            plans_dir = os.path.join(workspace, ".omo", "plans")

            lines = ["Usage: /start-work <plan-name>", ""]

            if os.path.isdir(plans_dir):

                plans = [f for f in os.listdir(plans_dir) if f.endswith(".md")]

                if plans:

                    lines.append("Available plans:")

                    for p in sorted(plans):

                        lines.append("  {}".format(p))

                else:

                    lines.append("No plans found in .omo/plans/")

            else:

                lines.append("No .omo/plans/ directory found.")

            return "\n".join(lines)



        plan_path = self._find_plan(plan_name)

        if plan_path is None:

            return "Error: Plan '{}' not found.\nUse /start-work without arguments to list available plans.".format(

                plan_name

            )



        try:

            with open(plan_path, "r", encoding="utf-8") as f:

                plan_content = f.read()

        except Exception as exc:

            return "Error: Failed to read plan file: {}".format(exc)



        # Verify executor agent exists

        from berserker.agent.manager import agent_manager



        try:

            agent_manager.get("executor")

        except KeyError:

            return "Error: 'executor' agent is not configured."



        # Auto-switch to executor

        original_agent = self.controller.current_agent

        if original_agent != "executor":

            self.controller.current_agent = "executor"

            self.controller.frame.set_agent_status("executor")

            if self.controller.frame.model_agent_bar:

                try:

                    agent = agent_manager.get("executor")

                    self.controller.current_model = agent.model

                    self.controller.frame.model_agent_bar.set_selected_agent("executor")

                    self.controller.frame.model_agent_bar.set_selected_model(agent.model)

                except KeyError:

                    pass



        # Build execution message

        plan_basename = os.path.basename(plan_path)

        exec_message = "Execute the following plan from '{}':\n\n{}".format(

            plan_basename, plan_content

        )



        # Save to session and trigger execution

        session_id = self.controller.session_id

        if session_id is None:

            return "Error: No active session."



        ctx = self.controller.get_session_context()

        if ctx is not None:

            ctx.append_message("user", exec_message)

        else:

            from berserker.session.manager import session_manager



            session_manager.append_message(session_id, "user", exec_message)



        # Build messages and execute

        if ctx is not None:

            messages = ctx.get_messages()

        else:

            from berserker.session.manager import session_manager



            messages = session_manager.get_messages(session_id)



        from berserker.provider.base import ChatMessage



        chat_messages = []

        for msg in messages:

            chat_messages.append(

                ChatMessage(

                    role=msg.get("role", "user"),

                    content=msg.get("content", ""),

                    tool_calls=msg.get("tool_calls"),

                    tool_call_id=msg.get("tool_call_id") or msg.get("tool_result_for"),

                )

            )



        # Execute agent in background thread (GUI pattern)

        self.controller.execute_agent(

            agent_manager,

            "executor",

            chat_messages,

            session_id,

            self.controller.frame.tool_registry

            if hasattr(self.controller.frame, "tool_registry")

            else None,

        )



        return ""  # Execution started, results will appear in chat





def _fail_stale_agent(agent_id):

    # type: (str) -> None

    """Fail any agent that is still marked as STATUS_RUNNING.

    

    This is a safety net for cases where the executor registers an agent

    but then encounters an unhandled exception before updating the status.

    """

    stale = agent_monitor.get_agent(agent_id)

    if stale is not None and stale.status == "running":

        logger.warning("Fixing stale agent monitor entry: %s (status=running after execution)", agent_id)

        agent_monitor.update_phase(agent_id, "done", "Failed: unexpected error", progress=1.0)

        agent_monitor.update_status(agent_id, STATUS_FAILED)





class GUIController(DisplayAdapter):

    """Controller that bridges berserker agent execution with wxPython GUI.



    Implements DisplayAdapter interface so the same conversation logic

    can work with GUI instead of CLI.

    """



    def __init__(self, frame, session_id=None, current_agent="berserker", workspace_id=None):

        # type: (PyBerserkerFrame, Optional[str], str, Optional[str]) -> None

        """Initialize the GUI controller.



        Args:

            frame: The PyBerserkerFrame instance to control.

            session_id: Current session ID.

            current_agent: Current agent name.

            workspace_id: Current workspace ID for sidebar integration.

        """

        self.frame = frame

        self.session_id = session_id  # type: Optional[str]

        self.current_agent = current_agent  # type: str

        self.current_model = None  # type: Optional[str]

        self.workspace_id = workspace_id  # type: Optional[str]

        self._abort_event = threading.Event()

        self._selection_result = None  # type: Optional[Dict[str, Any]]

        self._selection_event = threading.Event()

        self._permission_result = None  # type: Optional[bool]

        self._permission_event = threading.Event()

        self._tool_calls_displayed = False  # type: bool
        self._sidebar_panel = None  # type: Any
        self._is_executing = False  # type: bool
        self._session_context = None  # type: Optional[SessionContext]
        self._state_lock = threading.Lock()  # Protects _is_executing, _tool_calls_displayed
        # Slash command handler
        self.slash_handler = SlashCommandHandler(self)

        # Persist GUI-initiated message deletions to the session store
        self.frame.message_display.chat_list.set_delete_callback(self._on_message_deleted)

        logger.info("[CONTROLLER] Initialized with current_agent=%s", self.current_agent)


    def set_session_context(self, ctx):

        # type: (Optional[SessionContext]) -> None

        """Set the active session context.



        Args:

            ctx: The SessionContext to use, or None to clear.

        """

        self._session_context = ctx



    def reset_session_state(self):

        # type: () -> None

        """Reset all session-related state when switching workspaces.



        Clears session_id, session context, and execution state.

        The controller remains ready for a new session selection.

        """

        with self._state_lock:
            self.session_id = None
            self._session_context = None
            self._is_executing = False
            self._abort_event.clear()
            self._tool_calls_displayed = False
        logger.info("[CONTROLLER] Session state reset for workspace switch")


    def get_session_context(self):

        # type: () -> Optional[SessionContext]

        """Get the active session context.



        Returns:

            The current SessionContext, or None if not set.

        """

        return self._session_context



    def process_input(self, text):

        # type: (str) -> Tuple[bool, str]

        """Process user input, handling slash commands.



        Args:

            text: Raw user input.



        Returns:

            (is_handled, result):

            - is_handled=True: input was a slash command, result is display text

            - is_handled=False: pass through to agent

        """

        return self.slash_handler.process(text)



    # -----------------------------------------------------------------------

    # DisplayAdapter interface implementation

    # -----------------------------------------------------------------------



    def display_user_message(self, text):

        # type: (str) -> None

        """Display a user message."""

        self.frame.add_user_message(text)



    def display_assistant_message(self, text):

        # type: (str) -> None

        """Display an assistant message."""

        self.frame.add_assistant_message(text)



    def display_tool_call(self, tool_name, args, result):

        # type: (str, Dict[str, Any], Dict[str, Any]) -> None

        """Display a tool call and its result."""

        logger.debug("display_tool_call called for %s", tool_name)

        with self._state_lock:

            self._tool_calls_displayed = True

        self.frame.add_tool_call(tool_name, result)

        logger.debug("display_tool_call completed for %s", tool_name)



    def display_token_usage(self, usage):  # type: (Dict[str, int]) -> None

        """Display token usage information."""

        if usage:

            text = "Tokens: {} in / {} out / {} total".format(

                usage.get("prompt_tokens", 0),

                usage.get("completion_tokens", 0),

                usage.get("total_tokens", 0),

            )

            self.frame.set_status(text)

        else:

            self.frame.set_status("Tokens: N/A")



    def display_info(self, text):

        # type: (str) -> None

        """Display informational text."""

        # In GUI, info messages appear as assistant messages

        self.frame.add_assistant_message(text)



    def display_error(self, text):

        # type: (str) -> None

        """Display an error message."""

        self.frame.add_assistant_message("Error: {}".format(text))



    def request_permission(self, tool_name, args):

        # type: (str, Dict[str, Any]) -> bool

        """Request user permission for tool execution.



        First checks config-based permission rules. Only shows GUI prompt

        when the rule action is 'ask'. For 'allow' returns True immediately,

        for 'deny' returns False immediately.

        """

        from berserker.permission import ALLOWED, DENIED, permission_checker



        # Check config-based permission rules first

        perm_result = permission_checker.check(tool_name, args=args)



        if perm_result == ALLOWED:

            return True

        elif perm_result == DENIED:

            return False



        # needs_ask - check if session-wide allow is enabled

        if self.frame.is_allow_all_session():

            return True



        # Show permission prompt

        self._permission_result = None  # type: Optional[bool]

        self._permission_event = threading.Event()



        def on_respond(allowed):

            # type: (bool) -> None

            self._permission_result = allowed

            self._permission_event.set()



        self.frame.show_tool_permission(tool_name, args, on_respond)



        # Wait for user response (blocks the agent thread, not GUI thread)

        self._permission_event.wait()



        # Hide permission widget after response

        self.frame.hide_tool_permission()



        return self._permission_result if self._permission_result is not None else False



    def request_selection(self, question, options, allow_custom=False, multiple=False):

        # type: (str, List[Dict[str, str]], bool, bool) -> Optional[Dict[str, Any]]

        """Request user selection from options.



        This blocks until the user makes a selection via the GUI.

        """

        # Reset selection state

        self._selection_result = None

        self._selection_event.clear()



        # Show selection widget with callback

        def on_select(result):

            # type: (Dict[str, Any]) -> None

            self._selection_result = result

            self._selection_event.set()

            self.frame.hide_dynamic_widget()



        self.frame.show_selection(question, options, multiple, allow_custom, on_select)



        # Wait for user selection (blocks the agent thread, not GUI thread)

        self._selection_event.wait()



        return self._selection_result



    def request_input(self, prompt):

        # type: (str) -> Optional[str]

        """Request text input from user.



        For GUI, this is handled by the main input panel.

        """

        # This is not typically called in GUI mode since input comes from the main panel

        return None



    def display_instructions(self, summary):

        # type: (List[str]) -> None

        """Display loaded instruction file summaries."""

        if summary:

            text = "Instructions loaded:\n" + "\n".join("  - {}".format(item) for item in summary)

            self.frame.add_assistant_message(text)



    def display_agent_list(self, agents, current_agent):

        # type: (List[Dict[str, str]], str) -> None

        """Display available agents and current selection."""

        lines = ["Available agents:"]

        for agent in agents:

            current_marker = " (active)" if agent.get("name") == current_agent else ""

            lines.append(

                "  {} — {} [mode: {}]{}".format(

                    agent.get("name", ""),

                    agent.get("description", ""),

                    agent.get("mode", ""),

                    current_marker,

                )

            )

            lines.append("    Tools: {}".format(", ".join(agent.get("tools", []))))

        lines.append("")

        lines.append("Current agent: {}".format(current_agent))



        self.frame.add_assistant_message("\n".join(lines))



    def display_skills(self, skills):

        # type: (List[Dict[str, str]]) -> None

        """Display available skills."""

        if not skills:

            self.frame.add_assistant_message("No skills available.")

            return



        lines = ["Available skills:"]

        for skill in skills:

            name = skill.get("name", "unknown")

            desc = skill.get("description", "")

            if desc:

                lines.append("  {} — {}".format(name, desc))

            else:

                lines.append("  {}".format(name))



        self.frame.add_assistant_message("\n".join(lines))



    # -----------------------------------------------------------------------

    # Agent & Model switching (called from ModelAgentBar dropdowns)

    # -----------------------------------------------------------------------



    def switch_agent(self, agent_name):

        # type: (str) -> None

        """Switch the current agent and update status bar.



        Args:

            agent_name: Name of the agent to switch to.

        """

        from berserker.agent.manager import agent_manager



        try:

            agent = agent_manager.get(agent_name)

            self.current_agent = agent.name

            self.frame.set_agent_status(agent_name)

            # Auto-switch to the model configured for this agent

            if agent.model and self.frame.model_agent_bar:

                self.current_model = agent.model

                self.frame.model_agent_bar.set_selected_model(agent.model)

            logger.info("Switched to agent: %s", agent_name)

        except KeyError:

            logger.warning("Agent '%s' not found", agent_name)

            self.frame.add_assistant_message("Error: Agent '{}' not found.".format(agent_name))

            self.frame.set_agent_status(self.current_agent)

            # Reset dropdown to current agent

            self.frame.model_agent_bar.set_selected_agent(self.current_agent)



    def switch_model(self, model_spec):  # type: (str) -> None

        """Switch the current model.



        Args:

            model_spec: Model spec (e.g., 'openai/gpt-4').

        """

        self.current_model = model_spec
        logger.info("Switched to model: %s", model_spec)
        self.frame.set_status("Model: {}".format(model_spec))


    # -----------------------------------------------------------------------

    # Agent execution management

    # -----------------------------------------------------------------------

    def execute_agent(self, agent_manager, agent_name, messages, session_id, tool_registry):

        # type: (Any, str, List[Any], str, Any) -> None

        """Execute an agent turn in a background thread.



        Args:

            agent_manager: The AgentManager instance.

            agent_name: Name of the agent to execute.

            messages: List of ChatMessage objects.

            session_id: Current session ID.

            tool_registry: The ToolRegistry instance.

        """

        # Prevent concurrent agent execution (thread-safe)

        with self._state_lock:

            if self._is_executing:

                logger.warning("Agent already executing, ignoring new request")

                return

            # Reset abort event and set executing flag atomically

            self._abort_event.clear()

            self._is_executing = True



        # Disable UI controls during execution

        if hasattr(self.frame, "model_agent_bar") and self.frame.model_agent_bar:

            self.frame.model_agent_bar.disable_agent_selector()

            self.frame.model_agent_bar.disable_model_selector()

        if hasattr(self.frame, "sidebar_panel") and self.frame.sidebar_panel:

            self.frame.sidebar_panel.disable_session_list()

            self.frame.sidebar_panel.disable_new_session()



        # Update UI state (includes activity indicator, buttons, status)

        self.frame.set_processing_state(True)

        # Run agent in background thread

        thread = threading.Thread(

            target=self._run_agent,

            args=(agent_manager, agent_name, messages, session_id, tool_registry),

            daemon=True,

        )

        thread.start()



    def _run_agent(self, agent_manager, agent_name, messages, session_id, tool_registry):

        # type: (Any, str, List[Any], str, Any) -> None

        """Run agent execution in background thread."""
        # Reset tool call tracking for this run
        with self._state_lock:
            self._tool_calls_displayed = False
        try:
            # Create callbacks that use this controller
            def on_tool_call(tool_name, args, result):
                # type: (str, Dict[str, Any], Dict[str, Any]) -> None
                logger.debug("Controller on_tool_call called for %s", tool_name)
                wx.CallAfter(self.display_tool_call, tool_name, args, result)
                logger.debug("Controller wx.CallAfter scheduled for %s", tool_name)


            def on_permission_ask(tool_name, args):

                # type: (str, Dict[str, Any]) -> bool

                return self.request_permission(tool_name, args)



            # Build extra context for tools (includes selection callback)

            tool_extra = {

                "selection_callback": self.request_selection,

            }



            # Execute agent with abort event support

            result = agent_manager.execute(

                agent_name,

                messages,

                session_id,

                tool_registry,

                on_tool_call=on_tool_call,

                on_permission_ask=on_permission_ask,

                extra=tool_extra,

                abort_event=self._abort_event,

            )



            # Check for abort
            if result.get("finish_reason") == "abort":
                logger.info("Execution was aborted, keeping executed tool bubbles")
                wx.CallAfter(self._cleanup_after_abort)
                return
            # Extract response
            response = result.get("content", "")
            usage = result.get("usage")
            finish_reason = result.get("finish_reason", "stop")

            # Display response
            if response:
                wx.CallAfter(self.display_assistant_message, response)
            elif finish_reason == "tool_calls":
                pass  # Tool calls already displayed
            elif finish_reason == "content_filter":
                wx.CallAfter(self.display_assistant_message, "(response blocked by content filter)")
            elif finish_reason == "length":
                wx.CallAfter(
                    self.display_assistant_message, "(response truncated: max tokens reached)"
                )
            elif finish_reason == "refusal":
                wx.CallAfter(self.display_assistant_message, "(model refused to generate response)")
            elif finish_reason == "error":
                wx.CallAfter(self.display_assistant_message, "(error during generation)")
            elif self._tool_calls_displayed:
                pass  # Tool calls already displayed, no text response needed
            else:
                wx.CallAfter(self.display_assistant_message, "(empty response)")
            # Display token usage
            wx.CallAfter(self.display_token_usage, usage)


        except Exception as exc:

            logger.error("Agent execution failed: {}".format(exc))

            # Mark agent as failed in monitor (prevents stale "running" entries)

            agent_id = "{}-{}".format(agent_name, session_id)

            try:

                agent_monitor.update_phase(

                    agent_id, "done", "Failed: {}".format(exc), progress=1.0

                )

                agent_monitor.update_status(agent_id, STATUS_FAILED)

            except Exception:

                pass

            wx.CallAfter(self.display_error, "Agent execution failed: {}".format(exc))



        finally:

            # Restore UI state - must use wx.CallAfter since we're in a background thread

            wx.CallAfter(self.frame.set_processing_state, False)

            # Re-enable UI controls after execution

            if hasattr(self.frame, "model_agent_bar") and self.frame.model_agent_bar:

                wx.CallAfter(self.frame.model_agent_bar.enable_agent_selector)

                wx.CallAfter(self.frame.model_agent_bar.enable_model_selector)

            if hasattr(self.frame, "sidebar_panel") and self.frame.sidebar_panel:

                wx.CallAfter(self.frame.sidebar_panel.enable_session_list)

                wx.CallAfter(self.frame.sidebar_panel.enable_new_session)

            # Clear execution flag on main thread (thread-safe)

            wx.CallAfter(self._clear_executing_flag)

            # Safety: if any agent remains at STATUS_RUNNING, fail it

            agent_id = "{}-{}".format(agent_name, session_id)

            wx.CallAfter(_fail_stale_agent, agent_id)



    def _cleanup_after_abort(self):
        # type: () -> None
        """Handle UI state after an abort.

        Executed tool-call bubbles are intentionally kept: the agent persists
        each tool result to the session during execution, so removing the
        bubbles here would desync the on-screen history from storage.
        """
        self.frame.set_status("Execution stopped")


    def _clear_executing_flag(self):

        # type: () -> None

        """Clear the _is_executing flag on the main thread.



        Called via wx.CallAfter from _run_agent's finally block.

        Uses the state lock for thread safety.

        """

        with self._state_lock:

            self._is_executing = False



    def abort_execution(self):

        # type: () -> None

        """Signal the agent to abort execution."""

        self._abort_event.set()



    def get_abort_event(self):  # type: () -> threading.Event

        """Get the abort event for tool context."""

        return self._abort_event



    # -----------------------------------------------------------------------

    # Sidebar integration

    # -----------------------------------------------------------------------



    def setup_sidebar(self, sidebar_panel):

        # type: (Any) -> None

        """Register sidebar callbacks for session selection and creation.



        Args:

            sidebar_panel: A SidebarPanel instance with on_session_selected

                          and on_new_session callback registration methods.

        """

        self._sidebar_panel = sidebar_panel



        # Register session selection callback

        sidebar_panel.on_session_selected(self.on_sidebar_session_selected)



        # Register new session creation callback

        sidebar_panel.on_new_session(self.on_sidebar_new_session)



        # Register session deletion callback

        sidebar_panel.on_delete_session(self.on_sidebar_delete_session)



    def on_sidebar_session_selected(self, session_id):
        # type: (str) -> None
        """Handle session selection from sidebar.

        Args:
            session_id: The session ID selected in the sidebar.
        """
        # Block session switching during agent execution
        with self._state_lock:
            if self._is_executing:
                logger.warning("Cannot switch session during execution")
                wx.CallAfter(
                    self.frame.add_assistant_message,
                    "Cannot switch session while agent is executing. Please wait or abort first.",
                )
                return

        # Disable session list and new session button during loading
        if self._sidebar_panel is not None:
            self._sidebar_panel.disable_session_list()
            self._sidebar_panel.disable_new_session()

        try:
            self._switch_to_session(session_id)
        finally:
            # Re-enable session list and new session button after loading
            if self._sidebar_panel is not None:
                self._sidebar_panel.enable_session_list()
                self._sidebar_panel.enable_new_session()

    def _switch_to_session(self, session_id):
        # type: (str) -> bool
        """Switch the active session and display its message history.

        Shared by the sidebar session selection and the ``/session`` slash
        command so both paths behave identically.

        Returns:
            True if the session was found and loaded, False otherwise.
        """
        from berserker.session.manager import session_manager
        from berserker import __version__

        if not session_manager.session_exists(session_id):
            return False

        # Switch to the new session context
        ctx = session_manager.switch_to(session_id)
        self.session_id = session_id
        self.frame.session_id = session_id
        self.set_session_context(ctx)

        # Clear old messages and load the new session's history
        self.frame._clear_messages()
        self._load_session_messages(session_id)

        # Enable input now that a session is active
        self.frame.set_send_enabled(True)
        self.frame.SetTitle("berserker {} — Session: {}".format(__version__, session_id))
        return True


    def on_sidebar_new_session(self):

        # type: () -> None

        """Handle new session button click from sidebar.



        Creates a new session and switches to it immediately.

        """

        # Block new session creation during agent execution

        with self._state_lock:

            if self._is_executing:

                logger.warning("Cannot create new session during execution")

                wx.CallAfter(

                    self.frame.add_assistant_message,

                    "Cannot create new session while agent is executing. Please wait or abort first.",

                )

                return



        from berserker.session.manager import session_manager



        # Create new session

        new_session_id = session_manager.create()



        # Set workspace_id on the new session

        if self.workspace_id is not None:

            session_manager.set_workspace(new_session_id, self.workspace_id)



        # Switch to the new session context

        ctx = session_manager.switch_to(new_session_id)

        self.session_id = new_session_id

        self.frame.session_id = new_session_id

        self.set_session_context(ctx)



        # Clear messages and show welcome

        self.frame.clear_messages()

        self.frame.add_assistant_message("New session created: {}".format(new_session_id))



        # Update frame title

        from berserker import __version__



        self.frame.SetTitle("berserker {} — Session: {}".format(__version__, new_session_id))



        # Refresh sidebar to show the new session

        if self._sidebar_panel is not None:

            self._sidebar_panel.refresh_sessions()



        # Enable input now that a session is active

        self.frame.set_send_enabled(True)



    def on_sidebar_delete_session(self, session_id):

        # type: (str) -> None

        """Handle session deletion from sidebar context menu.



        Shows a confirmation dialog, deletes the session if confirmed,

        and refreshes the sidebar. If the deleted session was the current

        one, creates a new session.



        Args:

            session_id: The session ID to delete.

        """

        from berserker.session.manager import session_manager



        # Show confirmation dialog

        result = wx.MessageBox(

            "Delete this session?", "Confirm Delete", wx.YES_NO | wx.ICON_QUESTION

        )



        if result != wx.YES:

            return



        # Delete the session

        session_manager.delete(session_id)



        # Refresh sidebar

        if self._sidebar_panel is not None:

            self._sidebar_panel.refresh_sessions()



        # If the deleted session was the current one, switch to another or create new

        if self.session_id == session_id:

            # Try to find another session in the current workspace

            sessions = (

                session_manager.list_sessions_by_workspace(self.workspace_id)

                if self.workspace_id

                else []

            )

            if sessions:

                # Switch to the first available session

                new_session_id = sessions[0].get("id", "")

                self.on_sidebar_session_selected(new_session_id)

            else:

                # No sessions left, create a new one

                self.on_sidebar_new_session()



    def _load_session_messages(self, session_id):
        # type: (str) -> None
        """Load and display the most recent messages of a session.

        Only the most recent PAGE_SIZE messages are loaded initially; older
        messages are fetched on demand via the 'Load earlier messages' link.

        Uses windowed SQL reads and a single batched insertion
        (set_messages → one SetItemCount/Refresh) instead of per-message
        layout churn, and scrolls to the bottom so the newest messages are
        visible.

        Args:
            session_id: The session whose messages should be displayed.
        """
        from berserker.session.manager import session_manager

        PAGE_SIZE = 20
        total = session_manager.get_message_count(session_id)
        if total <= 0:
            return

        initial = session_manager.get_messages_window(session_id, PAGE_SIZE, 0)
        remaining_count = total - len(initial)

        # Add "Load earlier messages" button if there are older messages
        if remaining_count > 0:
            count = remaining_count
            sid = session_id
            self.frame.add_load_earlier_button(
                count,
                lambda sid=sid, count=count: self._load_earlier_messages(
                    sid, count
                ),
            )

        items = self._build_chat_items(initial)
        self.frame.set_messages(items, scroll_to_bottom=True)

    def _load_earlier_messages(self, session_id, remaining_count):
        # type: (str, int) -> None
        """Load earlier messages when the 'Load earlier' button is clicked.

        Fetches only the newly revealed batch (windowed SQL read) and inserts
        it at the front of the existing display — no full rebuild — while
        keeping the current viewport anchored to the same messages.

        Args:
            session_id: The session to load messages from.
            remaining_count: Number of messages still unloaded before the
                currently displayed batch.
        """
        from berserker.session.manager import session_manager

        PAGE_SIZE = 20
        batch = session_manager.get_messages_window(session_id, PAGE_SIZE, remaining_count)
        if not batch:
            return

        new_remaining = max(0, remaining_count - len(batch))

        # Refresh the "Load earlier" link with the updated remaining count
        self.frame.remove_load_earlier_button()
        if new_remaining > 0:
            count = new_remaining
            self.frame.add_load_earlier_button(
                count,
                lambda sid=session_id, count=count: self._load_earlier_messages(
                    sid, count
                ),
            )

        items = self._build_chat_items(batch)
        self.frame.insert_messages_at_front(items)

    def _build_chat_items(self, messages):
        # type: (List[Dict[str, Any]]) -> List[ChatMessageData]
        """Convert stored message dicts into display items.

        Preserves the original message timestamps and ids (needed for
        persistent delete) and mirrors the live tool-call header format.
        """
        items = []  # type: List[ChatMessageData]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            ts = self._fmt_ts(msg.get("created_at"))
            message_id = msg.get("id", "")
            if role == "user":
                items.append(ChatMessageData(
                    type="user", text=content, timestamp=ts, message_id=message_id,
                ))
            elif role == "assistant":
                items.append(ChatMessageData(
                    type="assistant", text=content, timestamp=ts, message_id=message_id,
                ))
            elif role == "tool":
                tool_name = msg.get("tool_name", "unknown")
                error = content[7:] if content.startswith("Error: ") else None
                header = "[{}] Error: {}".format(tool_name, error) if error else "[{}]".format(tool_name)
                text = header
                if content:
                    text = "{}\n{}".format(header, content)
                items.append(ChatMessageData(
                    type="tool", text=text, timestamp=ts, tool_name=tool_name,
                    message_id=message_id, collapsed=True,
                ))
        return items

    @staticmethod
    def _fmt_ts(created_at):
        # type: (Any) -> str
        """Format a stored epoch timestamp as HH:MM:SS (falls back to now)."""
        try:
            return datetime.fromtimestamp(int(created_at)).strftime("%H:%M:%S")
        except (TypeError, ValueError, OSError):
            return datetime.now().strftime("%H:%M:%S")

    def _on_message_deleted(self, message_id):
        # type: (str) -> None
        """Persist a GUI-initiated message deletion to the session store."""
        if not self.session_id or not message_id:
            return
        try:
            from berserker.session.manager import session_manager
            session_manager.delete_message(self.session_id, message_id)
        except Exception as exc:
            logger.warning("Failed to persist message deletion: %s", exc)
